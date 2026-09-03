"""Tests for project identity resolution, normalization, and caching.

FILE: tests/core/test_project.py
"""

import json
import subprocess
import tempfile
from pathlib import Path

from snodo.project import (
    cache_project_id,
    get_project_id,
    normalize_remote_url,
    resolve_project_id,
    scope_for_project_id,
)


def test_normalize_remote_url():
    """Test that all various git remote URL shapes collapse to host/org/repo."""
    urls = [
        "git@github.com:org/repo.git",
        "ssh://git@github.com/org/repo.git",
        "https://github.com/org/repo.git",
        "https://github.com/org/repo",
        "https://user:tok@github.com/org/repo.git",
        "ssh://git@github.com:22/org/repo.git",
        "https://github.com/org/repo/",
        "git@Github.com:Org/Repo.git",
        "https://github.com/org/repo.git/",
    ]
    for url in urls:
        assert normalize_remote_url(url) == "github.com/org/repo"


def test_normalize_remote_url_different_org_repo():
    """Test that different orgs or repos yield different normalized outputs."""
    url1 = "https://github.com/org1/repo1"
    url2 = "https://github.com/org2/repo2"
    assert normalize_remote_url(url1) != normalize_remote_url(url2)


def test_normalize_remote_url_credentials_stripped():
    """Verify that credentials or tokens never appear in the normalized URL."""
    url = "https://mytoken:x-oauth-basic@github.com/org/repo.git"
    normalized = normalize_remote_url(url)
    assert "mytoken" not in normalized
    assert "x-oauth-basic" not in normalized
    assert normalized == "github.com/org/repo"


def test_resolve_project_id_no_git():
    """If the directory is not a git repository, it returns a local UUID-based identity."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pid1, scope1 = resolve_project_id(tmpdir)
        pid2, scope2 = resolve_project_id(tmpdir)

        assert pid1.startswith("local:")
        assert scope1 == "local"
        assert pid2.startswith("local:")
        assert scope2 == "local"
        # Since it regenerates each time without cache, they must be different
        assert pid1 != pid2


def test_get_project_id_is_read_only():
    """Resolving an id for labelling creates no file — it must not change the filesystem."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)

        pid, scope = get_project_id(tmpdir)
        assert pid.startswith("local:")
        assert scope == "local"

        # No .snodo/project.json may be written as a side effect of reading.
        assert not (Path(tmpdir) / ".snodo" / "project.json").exists()


def test_get_project_id_repeated_resolution_performs_no_write():
    """A repeated resolution of an unchanged identity performs no write."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)

        # Establish a cached identity explicitly (as init does).
        cache_project_id(tmpdir, "github.com/org/repo", "remote")
        project_json = Path(tmpdir) / ".snodo" / "project.json"
        before = project_json.read_bytes()

        pid1, scope1 = get_project_id(tmpdir)
        pid2, scope2 = get_project_id(tmpdir)

        assert pid1 == pid2 == "github.com/org/repo"
        assert scope1 == scope2 == "remote"
        # The cache file is untouched by resolution.
        assert project_json.read_bytes() == before


def test_get_project_id_override_honors_project_id():
    """get_project_id honors a project.id override from project.json (read-only)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_project_id(tmpdir, "override-project-identity", "override")

        pid, scope = get_project_id(tmpdir)
        assert pid == "override-project-identity"
        assert scope == "override"


def test_scope_for_project_id():
    """Scope is derived from the id, so the two cannot disagree."""
    assert scope_for_project_id("local:6bd1d012554546c4b9462bfaaa4183d8") == "local"
    assert scope_for_project_id("github.com/org/repo") == "remote"
    assert scope_for_project_id("custom-override") == "remote"


def test_get_project_id_promotes_when_remote_appears():
    """A repository initialised without a remote promotes to the remote id once one exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)

        # Establish a local identity the way init does: resolve, then cache.
        pid1, scope1 = get_project_id(tmpdir)
        assert pid1.startswith("local:")
        assert scope1 == "local"
        cache_project_id(tmpdir, pid1, scope1)

        # A remote appears after initialisation.
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
            cwd=tmpdir,
            check=True,
        )

        # Next run resolves to the remote id, not the cached local id.
        pid2, scope2 = get_project_id(tmpdir)
        assert pid2 == "github.com/myorg/myrepo"
        assert scope2 == "remote"

        # Persisting the promotion is the caller's explicit step.
        cache_project_id(tmpdir, pid2, scope2)
        project_json = Path(tmpdir) / ".snodo" / "project.json"
        with open(project_json) as f:
            data = json.load(f)
        assert data["id"] == "github.com/myorg/myrepo"
        assert data["scope"] == "remote"


def test_get_project_id_keeps_local_id_across_runs():
    """A remote-less repository keeps its cached local id across runs (no re-mint)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)

        # Establish the local identity (as init does), then read it back.
        pid0, scope0 = get_project_id(tmpdir)
        cache_project_id(tmpdir, pid0, scope0)

        pid1, scope1 = get_project_id(tmpdir)
        pid2, scope2 = get_project_id(tmpdir)

        assert pid1.startswith("local:")
        assert scope1 == "local"
        assert pid2 == pid1
        assert scope2 == "local"


def test_get_project_id_remote_never_demotes():
    """A remote-scope project never demotes, even when the remote is removed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
            cwd=tmpdir,
            check=True,
        )

        pid1, scope1 = get_project_id(tmpdir)
        assert pid1 == "github.com/myorg/myrepo"
        assert scope1 == "remote"
        cache_project_id(tmpdir, pid1, scope1)

        subprocess.run(["git", "remote", "remove", "origin"], cwd=tmpdir, check=True)

        pid2, scope2 = get_project_id(tmpdir)
        assert pid2 == "github.com/myorg/myrepo"
        assert scope2 == "remote"


def test_cache_project_id():
    """Verify cache_project_id writes data correctly and preserves format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_project_id(tmpdir, "custom-id", "remote")

        project_json = Path(tmpdir) / ".snodo" / "project.json"
        assert project_json.exists()
        with open(project_json) as f:
            data = json.load(f)

        assert data["id"] == "custom-id"
        assert data["project.id"] == "custom-id"
        assert data["scope"] == "remote"


def test_is_system_root_or_temp(monkeypatch):
    """Verify system root and shared temp detection including dynamic runtime TMPDIR."""
    from snodo.project import _is_system_root_or_temp

    assert _is_system_root_or_temp(Path("/")) is True
    assert _is_system_root_or_temp(Path.home()) is True
    assert _is_system_root_or_temp(Path("/tmp")) is True
    assert _is_system_root_or_temp(Path("/var/tmp")) is True
    assert _is_system_root_or_temp(Path("/var/folders/2v/xyz/T")) is True
    assert _is_system_root_or_temp(Path("/private/var/folders/2v/xyz/T")) is True

    # Test dynamic runtime TMPDIR (e.g. /tmp/snodo-b-XXXX)
    with tempfile.TemporaryDirectory() as custom_tmp:
        monkeypatch.setenv("TMPDIR", custom_tmp)
        assert _is_system_root_or_temp(Path(custom_tmp)) is True
        # Child project directories inside custom TMPDIR are NOT system roots
        assert _is_system_root_or_temp(Path(custom_tmp) / "my_project") is False


def test_cache_project_id_refuses_system_roots(caplog):
    """Verify cache_project_id does not write .snodo directly in system root directories."""
    import logging

    with caplog.at_level(logging.WARNING):
        cache_project_id("/tmp", "test-tmp-id", "local")
        assert not (Path("/tmp") / ".snodo").exists()
        assert "Refusing to cache project ID in system root" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cache_project_id(str(Path.home()), "test-home-id", "local")
        # Ensure ~/.snodo/project.json was not created if ~/.snodo wasn't already a project
        assert "Refusing to cache project ID in system root" in caplog.text


def test_cache_project_id_warns_on_non_project_dir(caplog):
    """Verify cache_project_id logs a warning when creating .snodo in a directory with no project markers."""
    import logging

    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(logging.WARNING):
            cache_project_id(tmpdir, "test-id", "local")
            assert "created .snodo in non-project directory" in caplog.text


def test_tool_telemetry_does_not_create_snodo_in_uninitialized_dir():
    """Verify persist_tool_telemetry does not create .snodo in directories without .snodo."""
    from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)
            persist_tool_telemetry("task_test_001", {"turn_index": 1, "tool": "test"})
            assert not (Path(tmpdir) / ".snodo").exists()
        finally:
            os.chdir(orig_cwd)


