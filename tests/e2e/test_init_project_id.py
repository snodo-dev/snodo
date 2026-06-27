"""E2E tests for snodo init project ID caching and overrides (ADR 012 T2).

FILE: tests/e2e/test_init_project_id.py
"""

import json
import subprocess
from pathlib import Path

import yaml


def test_init_with_git_remote(snodo_cli):
    """Verify that init in a repository with a git remote normalizes and caches it."""
    tmp_dir = snodo_cli.home
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:myorg/myrepo.git"],
        cwd=str(tmp_dir),
        check=True,
    )

    r = snodo_cli(["init", "--template", "solo"])
    assert r.returncode == 0
    assert "Project ID:  github.com/myorg/myrepo (remote)" in r.stdout

    project_json = Path(tmp_dir) / ".snodo" / "project.json"
    assert project_json.exists()
    
    with open(project_json) as f:
        data = json.load(f)
    assert data["id"] == "github.com/myorg/myrepo"
    assert data["scope"] == "remote"


def test_init_without_git_remote(snodo_cli):
    """Verify that init in a repository without a remote caches a generated local ID."""
    tmp_dir = snodo_cli.home
    # git remote is not added by default in snodo_cli fixture

    r = snodo_cli(["init", "--template", "solo"])
    assert r.returncode == 0
    assert "Project ID:  local:" in r.stdout
    assert "(local)" in r.stdout

    project_json = Path(tmp_dir) / ".snodo" / "project.json"
    assert project_json.exists()

    with open(project_json) as f:
        data = json.load(f)
    assert data["id"].startswith("local:")
    assert data["scope"] == "local"


def test_init_with_project_id_override_flag(snodo_cli):
    """Verify that --project-id flag caches the specified override."""
    tmp_dir = snodo_cli.home

    r = snodo_cli(["init", "--template", "solo", "--project-id", "custom-override-flag"])
    assert r.returncode == 0
    assert "Project ID:  custom-override-flag (override)" in r.stdout

    project_json = Path(tmp_dir) / ".snodo" / "project.json"
    assert project_json.exists()

    with open(project_json) as f:
        data = json.load(f)
    assert data["id"] == "custom-override-flag"
    assert data["scope"] == "override"


def test_init_with_project_id_config_override(snodo_cli):
    """Verify config-file override is cached, and --project-id flag wins over config."""
    tmp_dir = snodo_cli.home
    
    # 1. Config override only
    home_dir = Path(tmp_dir) / "snodo_home"
    home_dir.mkdir(exist_ok=True)
    config_file = home_dir / "config.yml"
    config_data = {
        "project.id": "custom-override-config",
        "providers": {
            "openai": {"api_key": "test"}
        }
    }
    with open(config_file, "w") as f:
        yaml.safe_dump(config_data, f)
        
    r = snodo_cli(["init", "--template", "solo"])
    assert r.returncode == 0
    assert "Project ID:  custom-override-config (override)" in r.stdout
    
    project_json = Path(tmp_dir) / ".snodo" / "project.json"
    with open(project_json) as f:
        data = json.load(f)
    assert data["id"] == "custom-override-config"
    assert data["scope"] == "override"

    # 2. Both config override and CLI flag (CLI flag should win)
    r2 = snodo_cli(["init", "--template", "solo", "--force", "--project-id", "cli-wins"])
    assert r2.returncode == 0
    assert "Project ID:  cli-wins (override)" in r2.stdout
    
    with open(project_json) as f:
        data2 = json.load(f)
    assert data2["id"] == "cli-wins"
    assert data2["scope"] == "override"
