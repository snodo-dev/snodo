"""Tests for project identity resolution, normalization, and caching.

FILE: tests/core/test_project.py
"""

import json
import tempfile
from pathlib import Path


from snodo.project import (
    normalize_remote_url,
    resolve_project_id,
    get_project_id,
    cache_project_id,
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
