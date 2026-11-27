"""The suite-repository .snodo tripwire detects every file mutation."""

from pathlib import Path

import pytest

from tests.conftest import _assert_suite_snodo_unchanged, _snodo_dir_state


@pytest.mark.parametrize("mutation", ["create", "modify", "delete", "rewrite"])
def test_snodo_guard_reports_changed_file(tmp_path, mutation):
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir()
    file_path = snodo_dir / "state.json"
    if mutation != "create":
        file_path.write_text("original content\n")
    before = _snodo_dir_state(tmp_path)

    if mutation == "create":
        file_path.write_text("new content\n")
    elif mutation == "modify":
        file_path.write_text("changed content, different size\n")
    elif mutation == "delete":
        file_path.unlink()
    else:
        file_path.write_text("rewritten content\n")
        # Make the metadata change deterministic even on coarse-timestamp filesystems.
        stat = file_path.stat()
        import os

        os.utime(file_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    after = _snodo_dir_state(tmp_path)
    assert before != after
    with pytest.raises(pytest.fail.Exception, match=str(file_path)):
        _assert_suite_snodo_unchanged(tmp_path, before, after, "test_node")


def test_snodo_guard_accepts_untouched_directory(tmp_path):
    file_path = tmp_path / ".snodo" / "state.json"
    file_path.parent.mkdir()
    file_path.write_text("unchanged\n")
    before = _snodo_dir_state(tmp_path)

    after = _snodo_dir_state(tmp_path)

    assert before == after
    _assert_suite_snodo_unchanged(tmp_path, before, after, "test_node")


def test_snodo_guard_handles_missing_directory(tmp_path):
    before = _snodo_dir_state(tmp_path)

    assert before is None
    _assert_suite_snodo_unchanged(tmp_path, before, None, "test_node")


def test_snodo_fingerprint_does_not_read_file_contents(tmp_path, monkeypatch):
    file_path = tmp_path / ".snodo" / "state.json"
    file_path.parent.mkdir()
    file_path.write_text("content")

    def fail_read(*args, **kwargs):
        raise AssertionError("fingerprinting must not read file contents")

    monkeypatch.setattr(Path, "open", fail_read)

    assert _snodo_dir_state(tmp_path) == {
        "state.json": (file_path.stat().st_size, file_path.stat().st_mtime_ns)
    }
