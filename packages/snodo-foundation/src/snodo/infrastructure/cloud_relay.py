"""Outbound cloud WebSocket relay for locally running serve endpoints."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import secrets
import random
import inspect
from pathlib import Path
from urllib.parse import quote

import httpx
import websockets

from snodo.config import get_cloud_lease_url, get_cloud_ingest_url
from snodo.infrastructure.cloud_lease import mint_lease
from snodo.infrastructure.relay_frame_codec import ChunkReassembler, decode_frame, encode_frame
from snodo.infrastructure.serve_registry import list_serve_processes
from snodo.paths import resolve_home
from snodo.version import __version__

_LOG = logging.getLogger(__name__)
class _ReplacedConnection(Exception):
    code = 4001


MAX_IN_FLIGHT = 8
KEEP_SECONDS = 30.0
REGISTRY_POLL_SECONDS = 0.25


def machine_id(path: Path | None = None) -> str:
    """Load/create a random, owner-only machine identifier."""
    target = path or (resolve_home() / "machine_id")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        value = target.read_text(encoding="ascii").strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(24)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(value + "\n")
    return value


def websocket_url(config: dict, machine: str) -> str:
    """Derive runner WebSocket origin from the configured cloud app URL."""
    base = get_cloud_lease_url(config) or get_cloud_ingest_url(config)
    # lease_url is the same app host used by all cloud calls.
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/runner/connect", f"machine_id={quote(machine)}", ""))


def _endpoints(entries: list[dict]) -> list[dict[str, str]]:
    return [{"hostname": row["hostname"], "auth": ["oauth"]} for row in entries
            if row.get("hostname") and row.get("port")]


class CloudRelay:
    """Runs one outbound connection and multiplexes bounded local HTTP requests."""

    def __init__(self, config: dict, *, socket_connect=None, lease_fetch=None, registry=None,
                 http_client=None, sleep=asyncio.sleep, clock=None, machine=None,
                 logger: logging.Logger | None = None) -> None:
        self.config = config
        self.socket_connect = socket_connect or websockets.connect
        self.lease_fetch = lease_fetch or mint_lease
        self.registry = registry or list_serve_processes
        self.http = http_client or httpx.AsyncClient(timeout=None)
        self._owns_http = http_client is None
        self.sleep = sleep
        self.machine = machine or machine_id()
        self.log = logger or _LOG
        self._clock = clock or time.monotonic
        self._slots = asyncio.Semaphore(MAX_IN_FLIGHT)
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._request_frames: dict[str, dict] = {}

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def run(self, *, max_connections: int | None = None) -> None:
        """Reconnect indefinitely; max_connections is a deterministic test seam."""
        delay, count = 1.0, 0
        while max_connections is None or count < max_connections:
            count += 1
            started = self._clock()
            try:
                await self._connection()
            except Exception as exc:  # status exceptions intentionally reduced to code/status
                status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
                code = getattr(exc, "code", None)
                if code == 4001:
                    self.log.warning("relay stopped: replaced by a newer runner connection")
                    return
                if status in (403, 426):
                    self.log.warning("relay connection rejected status=%s", status)
                    return
                self.log.warning("relay disconnected status=%s", status or "connection")
            elapsed = self._clock() - started
            wait = min(60.0, min(60.0, delay) * random.uniform(0.8, 1.2))  # noqa: S311 — reconnect jitter is not security-sensitive
            await self.sleep(wait)
            delay = min(60.0, delay * 2) if elapsed < 60 else 1.0

    async def _connection(self) -> None:
        lease = await self._fetch_lease()
        if lease is None:
            self.log.warning("cloud login required: sign in with the cloud login")
            raise ConnectionError("cloud lease unavailable")
        url = websocket_url(self.config, self.machine)
        try:
            socket = await self.socket_connect(url, additional_headers={"Authorization": f"Bearer {lease.token}"}, ping_interval=20)
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status != 401:
                raise
            lease = await self._fetch_lease(force=True)
            if lease is None:
                self.log.warning("cloud login required: sign in with the cloud login")
                raise ConnectionError("cloud lease unavailable") from None
            socket = await self.socket_connect(url, additional_headers={"Authorization": f"Bearer {lease.token}"}, ping_interval=20)
        async with socket:
            current = _endpoints(self.registry())
            pending: dict[str, ChunkReassembler] = {}
            await socket.send(encode_frame({"t": "hello", "v": 1, "machine_id": self.machine,
                                            "snodo_version": __version__, "endpoints": current}))
            iterator = socket.__aiter__()
            while True:
                try:
                    raw = await asyncio.wait_for(iterator.__anext__(), timeout=REGISTRY_POLL_SECONDS)
                except asyncio.TimeoutError:
                    raw = None
                except StopAsyncIteration:
                    break
                if raw is not None:
                    frame = decode_frame(raw)
                    if frame["t"] == "req":
                        rid = frame["id"]
                        self._request_frames[rid] = frame
                        pending[rid] = ChunkReassembler(rid)
                        length = frame["headers"].get("content-length", frame["headers"].get("Content-Length", "0"))
                        if not str(length).isdigit() or int(length) == 0:
                            self._tasks[rid] = asyncio.create_task(self._dispatch(socket, frame, b""))
                    elif frame["t"] == "chunk":
                        rid = frame["id"]
                        assembler = pending.setdefault(rid, ChunkReassembler(rid))
                        body = assembler.add(frame)
                        if body is not None:
                            req = self._request_frames.get(rid)
                            if req:
                                self._tasks[rid] = asyncio.create_task(self._dispatch(socket, req, body))
                    elif frame["t"] == "cancel":
                        self._cancelled.add(frame["id"])
                        task = self._tasks.pop(frame["id"], None)
                        if task:
                            task.cancel()
                entries = self.registry()
                endpoints = _endpoints(entries)
                if endpoints != current:
                    current = endpoints
                    await socket.send(encode_frame({"t": "hello", "v": 1, "machine_id": self.machine,
                                                    "snodo_version": __version__, "endpoints": endpoints}))
        if getattr(socket, "close_code", None) == 4001:
            raise _ReplacedConnection()

    async def _fetch_lease(self, *, force: bool = False):
        """Run the synchronous lease exchange off-loop; preserve async test seams."""
        kwargs = {"force": force} if force else {}
        if inspect.iscoroutinefunction(self.lease_fetch):
            return await self.lease_fetch(self.config, "runner", self.machine, **kwargs)
        return await asyncio.to_thread(self.lease_fetch, self.config, "runner", self.machine, **kwargs)

    async def _dispatch(self, socket, request: dict, body: bytes) -> None:
        rid = request["id"]
        async with self._slots:
            if rid in self._cancelled:
                return
            matches = [entry for entry in self.registry() if entry.get("hostname") == request["hostname"]]
            if not matches:
                await socket.send(encode_frame({"t": "err", "id": rid, "code": "unknown_endpoint", "message": "Unknown endpoint"}))
                return
            url = f"http://127.0.0.1:{matches[0]['port']}{request['path']}"
            keep = asyncio.create_task(self._keep(socket, rid))
            try:
                async with self.http.stream(request["method"], url, headers=request["headers"], content=body) as response:
                    await socket.send(encode_frame({"t": "res", "id": rid, "status": response.status_code,
                                                    "headers": dict(response.headers)}))
                    seq = 0
                    async for piece in response.aiter_bytes():
                        if rid in self._cancelled:
                            return
                        for offset in range(0, len(piece), 192 * 1024):
                            import base64
                            data = piece[offset:offset + 192 * 1024]
                            await socket.send(encode_frame({"t": "chunk", "id": rid, "seq": seq, "last": False, "data_b64": base64.b64encode(data).decode("ascii")}))
                            seq += 1
                    await socket.send(encode_frame({"t": "chunk", "id": rid, "seq": seq, "last": True, "data_b64": ""}))
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException):
                await socket.send(encode_frame({"t": "err", "id": rid, "code": "local_unavailable", "message": "Local endpoint unavailable"}))
            finally:
                keep.cancel()
                self._cancelled.discard(rid)
                self._request_frames.pop(rid, None)

    async def _keep(self, socket, rid: str) -> None:
        while True:
            await self.sleep(KEEP_SECONDS)
            await socket.send(encode_frame({"t": "keep", "id": rid}))
