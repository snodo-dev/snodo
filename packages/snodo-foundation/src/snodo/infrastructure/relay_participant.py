"""Coordinate one cloud relay owner across all serve processes on a machine."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import threading
from pathlib import Path
from typing import Callable

from snodo.infrastructure.cloud_relay import CloudRelay
from snodo.paths import resolve_home

_LOG = logging.getLogger(__name__)


class RelayParticipant:
    """Run a relay while holding the machine-wide exclusive ownership lock."""

    def __init__(self, relay_factory: Callable[[], CloudRelay], *, lock_path: Path | None = None,
                 retry_interval: float = 1.0, logger: logging.Logger | None = None,
                 notify: Callable[[str], None] | None = None) -> None:
        self.relay_factory = relay_factory
        self.lock_path = lock_path or (resolve_home() / "machine_id.relay.lock")
        self.retry_interval = retry_interval
        self.log = logger or _LOG
        self.notify = notify
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._relay_task: asyncio.Task | None = None
        self._guard = threading.Lock()

    def start(self) -> None:
        """Start participating in relay ownership; repeated starts are harmless."""
        with self._guard:
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="snodo-cloud-relay", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop participation and wait for the relay and ownership lock to end."""
        with self._guard:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            loop, task = self._loop, self._relay_task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        if thread is not threading.current_thread():
            thread.join()
        with self._guard:
            self._thread = None
            self._loop = None
            self._relay_task = None

    def _run(self) -> None:
        waited = False
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_path.open("a") as lock_file:
            while not self._stop.is_set():
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if not waited:
                        waited = True
                        self._notify("Another snodo serve on this machine is holding the connection to snodo cloud; this serve will take over if it stops.")
                    self._stop.wait(self.retry_interval)
                    continue
                try:
                    if self._stop.is_set():
                        return
                    if waited:
                        self._notify("This serve now holds the connection to snodo cloud.")
                    relay = self.relay_factory()
                    asyncio.run(self._run_relay(relay))
                    # A relay returning normally is terminal too; do not spin.
                    return
                except Exception:
                    self.log.exception("cloud relay participant failed")
                    return
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _notify(self, message: str) -> None:
        if self.notify is not None:
            try:
                self.notify(message)
            except Exception:
                self.log.exception("relay participant notification failed")

    async def _run_relay(self, relay: CloudRelay) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(relay.run())
        with self._guard:
            self._loop, self._relay_task = loop, task
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            close = getattr(relay, "close", None)
            if close is not None:
                await close()
            with self._guard:
                self._loop = self._relay_task = None
