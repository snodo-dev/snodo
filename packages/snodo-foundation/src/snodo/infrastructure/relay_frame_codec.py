"""Pure JSON codec and bounded body chunk utilities for the cloud relay."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Iterator
from typing import Any, TypedDict

CHUNK_SIZE = 192 * 1024
DEFAULT_BODY_LIMIT = 64 * 1024 * 1024


class RelayFrameError(ValueError):
    """Invalid relay frame or body chunk sequence."""


class Frame(TypedDict, total=False):
    t: str
    v: int
    machine_id: str
    snodo_version: str
    endpoints: list[dict[str, str]]
    id: str
    status: int
    headers: dict[str, str]
    seq: int
    last: bool
    data_b64: str
    code: str
    message: str
    hostname: str
    method: str
    path: str


_REQUIRED: dict[str, dict[str, type]] = {
    "hello": {"v": int, "machine_id": str, "snodo_version": str, "endpoints": list},
    "res": {"id": str, "status": int, "headers": dict},
    "chunk": {"id": str, "seq": int, "last": bool, "data_b64": str},
    "err": {"id": str, "code": str, "message": str},
    "keep": {"id": str},
    "req": {"id": str, "hostname": str, "method": str, "path": str, "headers": dict},
    "cancel": {"id": str},
}


def encode_frame(frame: Frame | dict[str, Any]) -> str:
    """Encode a plain frame mapping as compact JSON."""
    validate_frame(frame)
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=False)


def decode_frame(text: str) -> Frame:
    """Decode and validate a JSON relay frame without exposing input in errors."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise RelayFrameError("invalid JSON frame") from exc
    if not isinstance(value, dict):
        raise RelayFrameError("frame must be an object")
    validate_frame(value)
    if value["t"] == "chunk":
        try:
            raw = base64.b64decode(value["data_b64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RelayFrameError("invalid chunk base64") from exc
        if len(raw) > CHUNK_SIZE:
            raise RelayFrameError("chunk exceeds maximum size")
    return value


def validate_frame(frame: dict[str, Any]) -> None:
    kind = frame.get("t")
    if not isinstance(kind, str) or kind not in _REQUIRED:
        raise RelayFrameError("unknown frame type")
    for key, expected in _REQUIRED[kind].items():
        if key not in frame or not isinstance(frame[key], expected) or (expected is int and isinstance(frame[key], bool)):
            raise RelayFrameError("missing or invalid frame field")
    if kind == "chunk":
        if frame["seq"] < 0:
            raise RelayFrameError("invalid chunk sequence")
        if not isinstance(frame["last"], bool):
            raise RelayFrameError("missing or invalid frame field")
        try:
            raw = base64.b64decode(frame["data_b64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RelayFrameError("invalid chunk base64") from exc
        if len(raw) > CHUNK_SIZE:
            raise RelayFrameError("chunk exceeds maximum size")


def split_body(request_id: str, body: bytes | Iterable[bytes]) -> Iterator[Frame]:
    """Yield base64 chunk frames in order, buffering at most one raw chunk."""
    pieces = (body,) if isinstance(body, bytes) else body
    pending = bytearray()
    seq = 0
    emitted = False
    previous: Frame | None = None
    for piece in pieces:
        if not isinstance(piece, bytes):
            raise TypeError("body pieces must be bytes")
        view = memoryview(piece)
        offset = 0
        while offset < len(view):
            take = min(CHUNK_SIZE - len(pending), len(view) - offset)
            pending.extend(view[offset : offset + take])
            offset += take
            if len(pending) == CHUNK_SIZE:
                # Defer a full chunk until more input establishes whether it is last.
                yield_frame = {"t": "chunk", "id": request_id, "seq": seq, "last": False,
                               "data_b64": base64.b64encode(pending).decode("ascii")}
                # Keep one full chunk as look-behind to mark terminal chunks correctly.
                if emitted:
                    assert previous is not None
                    yield previous
                previous = yield_frame
                emitted = True
                seq += 1
                pending.clear()
    if pending or not emitted:
        final = {"t": "chunk", "id": request_id, "seq": seq,
                 "last": True, "data_b64": base64.b64encode(pending).decode("ascii")}
        if emitted:
            assert previous is not None
            yield previous
        yield final
    else:
        assert previous is not None
        previous["last"] = True
        yield previous


class ChunkReassembler:
    """Reassemble one request's consecutive chunk frames under a byte ceiling."""

    def __init__(self, request_id: str, max_body_size: int = DEFAULT_BODY_LIMIT) -> None:
        if max_body_size < 0:
            raise ValueError("max_body_size must be nonnegative")
        self.request_id = request_id
        self.max_body_size = max_body_size
        self._next_seq = 0
        self._body = bytearray()
        self._finished = False

    def add(self, frame: Frame | dict[str, Any]) -> bytes | None:
        validate_frame(frame)
        if frame["t"] != "chunk" or frame["id"] != self.request_id:
            raise RelayFrameError("chunk does not belong to this request")
        if self._finished:
            raise RelayFrameError("chunk follows final chunk")
        if frame["seq"] != self._next_seq:
            raise RelayFrameError("chunks are out of order")
        data = base64.b64decode(frame["data_b64"], validate=True)
        if len(self._body) + len(data) > self.max_body_size:
            raise RelayFrameError("body exceeds configured limit")
        self._body.extend(data)
        self._next_seq += 1
        if frame["last"]:
            self._finished = True
            return bytes(self._body)
        return None
