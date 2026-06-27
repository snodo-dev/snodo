"""Project identity resolution, normalization, and caching (ADR 012).

FILE: snodo/project.py
"""

import json
import re
import subprocess
import uuid
from pathlib import Path


def normalize_remote_url(url: str) -> str:
    """Collapses remote URL to host/org/repo format."""
    # 1. Lowercase
    s = url.strip().lower()

    # 2. Strip scheme (https://, ssh://, git://, etc.)
    s = re.sub(r'^[a-z]+://', '', s)

    # 3. Strip userinfo/credentials (e.g. git@ or user:pass@)
    s = re.sub(r'^[^@]+@', '', s)

    # 4. Strip default ports (:22 and :443) before host/path separator
    # E.g. github.com:22/org/repo -> github.com/org/repo
    s = re.sub(r':(22|443)(/|$)', r'\2', s)

    # 5. Handle scp-like host:path separator (replace ':' with '/')
    # E.g. github.com:org/repo -> github.com/org/repo
    # Negative lookahead ensures we don't match other port forms like github.com:8080/org/repo
    s = re.sub(r'^([a-z0-9.-]+):(?![0-9]+/)(.*)', r'\1/\2', s)

    # 6. Strip trailing .git and slashes
    s = s.rstrip('/')
    s = re.sub(r'\.git$', '', s)
    s = s.rstrip('/')

    return s


def resolve_project_id(project_root: str) -> tuple[str, str]:
    """Resolves project identity by checking git remotes or generating a local UUID."""
    try:
        # Try origin remote first
        res = subprocess.run(
            ["git", "-C", project_root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False
        )
        url = res.stdout.strip()
        if res.returncode == 0 and url:
            return (normalize_remote_url(url), "remote")

        # Fallback to the first remote listed
        res_list = subprocess.run(
            ["git", "-C", project_root, "remote"],
            capture_output=True,
            text=True,
            check=False
        )
        if res_list.returncode == 0:
            remotes = [r.strip() for r in res_list.stdout.splitlines() if r.strip()]
            if remotes:
                res_url = subprocess.run(
                    ["git", "-C", project_root, "remote", "get-url", remotes[0]],
                    capture_output=True,
                    text=True,
                    check=False
                )
                url = res_url.stdout.strip()
                if res_url.returncode == 0 and url:
                    return (normalize_remote_url(url), "remote")
    except Exception:
        pass

    return ("local:" + uuid.uuid4().hex, "local")


def get_project_id(project_root: str) -> tuple[str, str]:
    """Retrieve the project ID and scope, utilizing .snodo/project.json cache/override."""
    project_json_path = Path(project_root) / ".snodo" / "project.json"
    if project_json_path.exists():
        try:
            with open(project_json_path) as f:
                data = json.load(f)
            pid = data.get("project.id") or data.get("id")
            scope = data.get("scope", "local")
            if pid:
                return (pid, scope)
        except Exception:
            pass

    # Resolve, cache, and return
    pid, scope = resolve_project_id(project_root)
    cache_project_id(project_root, pid, scope)
    return (pid, scope)


def cache_project_id(project_root: str, project_id: str, scope: str) -> None:
    """Caches the project ID and scope to .snodo/project.json."""
    snodo_dir = Path(project_root) / ".snodo"
    snodo_dir.mkdir(parents=True, exist_ok=True)
    project_json_path = snodo_dir / "project.json"
    try:
        data = {}
        if project_json_path.exists():
            with open(project_json_path) as f:
                data = json.load(f)
        data["id"] = project_id
        data["project.id"] = project_id
        data["scope"] = scope
        with open(project_json_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
