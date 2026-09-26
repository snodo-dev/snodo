"""Concurrent-process guarantees for shared atomic JSON writes."""

import json
import multiprocessing
import time
from pathlib import Path

from snodo.infrastructure.atomic_json import atomic_write_json


def _write_repeatedly(path: str, writer: int) -> None:
    payload = {"writer": writer, "body": "x" * 100_000}
    for _ in range(30):
        atomic_write_json(path, payload)


def test_concurrent_process_writes_never_publish_empty_or_partial_json(tmp_path: Path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"writer": -1, "body": "initial"})

    context = multiprocessing.get_context("fork")
    writers = 4
    processes = [
        context.Process(target=_write_repeatedly, args=(str(path), writer))
        for writer in range(writers)
    ]
    for process in processes:
        process.start()

    errors = []
    while any(process.is_alive() for process in processes):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(data["writer"], int)
            assert data["body"]
        except Exception as exc:
            errors.append(exc)
            break
        time.sleep(0.001)

    for process in processes:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0

    assert not errors, f"reader observed invalid JSON during writes: {errors}"
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["writer"] in range(writers)
    assert len(final["body"]) == 100_000
    assert not list(tmp_path.glob(".state.json-*.tmp"))
