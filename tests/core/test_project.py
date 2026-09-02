"""Tests for project identity resolution, normalization, and caching.

FILE: tests/core/test_project.py
"""

import json
import tempfile
from pathlib import Path

from snodo.project import (
    cache_project_id,
    get_project_id,
    normalize_remote_url,
    resolve_project_id,
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


def test_get_project_id_caching_and_override():
    """Verify get_project_id caching behavior and project.json override honors project.id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # First call resolves and caches
        pid1, scope1 = get_project_id(tmpdir)
        assert pid1.startswith("local:")
        assert scope1 == "local"

        # Second call returns cached values (same UUID)
        pid2, scope2 = get_project_id(tmpdir)
        assert pid1 == pid2
        assert scope1 == scope2

        # Override project.id in project.json
        project_json = Path(tmpdir) / ".snodo" / "project.json"
        with open(project_json) as f:
            data = json.load(f)

        data["project.id"] = "override-project-identity"
        with open(project_json, "w") as f:
            json.dump(data, f)

        pid3, scope3 = get_project_id(tmpdir)
        assert pid3 == "override-project-identity"
        assert scope3 == "local"


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


