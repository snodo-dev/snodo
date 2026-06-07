"""Tests for project root resolution (walk-up resolver).

FILE: tests/infrastructure/test_paths.py
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.infrastructure.paths import resolve_project_root, require_project_root


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
