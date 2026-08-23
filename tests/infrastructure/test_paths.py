"""Tests for project root resolution (walk-up resolver).

FILE: tests/infrastructure/test_paths.py
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.infrastructure.paths import (
    resolve_home,
    resolve_project_root,
    require_project_root,
)
from snodo.paths import derive_task_id


class TestResolveHome:
    def test_tilde_path_expands_to_absolute(self, monkeypatch):
        monkeypatch.setenv("SNODO_HOME", "~/snodo_test")
        result = resolve_home()
        assert result.is_absolute()
        assert "~" not in str(result)

    def test_absolute_path_returned_as_is(self, monkeypatch):
        monkeypatch.setenv("SNODO_HOME", "/tmp/snodo_abs")
        result = resolve_home()
        assert result == Path("/tmp/snodo_abs")

    def test_unset_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("SNODO_HOME", raising=False)
        result = resolve_home()
        assert result == Path.home() / ".snodo"


class TestResolveProjectRoot:
    def test_finds_snodo_in_current_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "myproject"
            project_root.mkdir()
            (project_root / ".snodo").mkdir()
            result = resolve_project_root(str(project_root))
            assert result == str(project_root.resolve())

    def test_finds_snodo_in_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "myproject"
            project_root.mkdir()
            (project_root / ".snodo").mkdir()

            subdir = project_root / "src" / "lib" / "deep"
            subdir.mkdir(parents=True)
            result = resolve_project_root(str(subdir))
            assert result == str(project_root.resolve())

    def test_returns_none_when_no_snodo_up_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodir = Path(tmp) / "no_snodo_here"
            nodir.mkdir()
            result = resolve_project_root(str(nodir))
            assert result is None

    def test_stops_at_filesystem_root(self):
        with patch.object(Path, "cwd", return_value=Path("/")):
            result = resolve_project_root()
            assert result is None

    def test_returns_none_for_nonexistent_start(self):
        result = resolve_project_root("/does/not/exist/anywhere")
        assert result is None

    def test_project_id_identical_from_root_and_subfolder(self):
        """Core bug fix: project_id must be the same from root or subfolder."""
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "myproject"
            project_root.mkdir()
            (project_root / ".snodo").mkdir()

            subdir = project_root / "src" / "components"
            subdir.mkdir(parents=True)

            root_from_root = resolve_project_root(str(project_root))
            root_from_sub = resolve_project_root(str(subdir))

            assert root_from_root == root_from_sub
            id_from_root = hashlib.sha256(root_from_root.encode()).hexdigest()[:16]
            id_from_sub = hashlib.sha256(root_from_sub.encode()).hexdigest()[:16]
            assert id_from_root == id_from_sub


class TestDeriveTaskId:
    def test_stable_digest_format(self):
        tid = derive_task_id("implement hello world")
        assert tid.startswith("task_")
        assert len(tid) == len("task_") + 12  # 48-bit hex digest
        assert all(c in "0123456789abcdef" for c in tid[len("task_"):])

    def test_deterministic_within_process(self):
        assert derive_task_id("same spec") == derive_task_id("same spec")

    def test_salt_independent_across_processes(self):
        """Same description must yield the same id under different PYTHONHASHSEED.

        Regresses the P1 where ``hash()`` (salted per interpreter) produced a
        different id on every run.
        """
        import os
        import subprocess
        import sys

        snippet = (
            "from snodo.paths import derive_task_id; "
            "print(derive_task_id('deterministic spec'))"
        )
        ids = set()
        for seed in ("0", "1", "42", "random"):
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            out = subprocess.run(
                [sys.executable, "-c", snippet],
                capture_output=True, text=True, env=env, check=True,
            ).stdout.strip()
            ids.add(out)
        assert len(ids) == 1, f"non-deterministic task ids: {ids}"

    def test_different_descriptions_differ(self):
        assert derive_task_id("a") != derive_task_id("b")


class TestRequireProjectRoot:
    def test_returns_root_when_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "myproject"
            project_root.mkdir()
            (project_root / ".snodo").mkdir()
            result = require_project_root(str(project_root))
            assert result == str(project_root.resolve())

    def test_raises_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodir = Path(tmp) / "no_snodo_here"
            nodir.mkdir()
            with pytest.raises(SystemExit):
                require_project_root(str(nodir))
