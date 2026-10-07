"""Git diff rendering for ``snodo task show --diff``."""

from typing import Any
from pathlib import Path


def task_diff(project_root: str, task_id: str, failure_entry: Any, halt_entry: Any, max_lines: int) -> dict:
    """Read a preserved task branch's stat and patch using Git only."""
    from snodo.tools.git import open_repo, resolve_base_branch

    branch = (failure_entry or {}).get("branch") if isinstance(failure_entry, dict) else None
    state_file = Path(project_root) / ".snodo" / "tasks" / task_id / "state.json"
    try:
        import json
        state = json.loads(state_file.read_text()) if state_file.is_file() else {}
    except (OSError, ValueError):
        state = {}
    branch = branch or state.get("branch")
    if not branch:
        try:
            from snodo.infrastructure.worktree import _task_identity
            spec = (halt_entry or {}).get("task_spec", "") if isinstance(halt_entry, dict) else ""
            _, branch = _task_identity(project_root, task_id, spec, None)
        except Exception:
            branch = f"task/{task_id}"
    validators: dict[str, list[dict]] = {}
    results = (halt_entry or {}).get("validator_results", []) if isinstance(halt_entry, dict) else []
    for verdict in results if isinstance(results, list) else []:
        if isinstance(verdict, dict):
            validators.setdefault(str(verdict.get("validator_id") or "?"), []).append({
                "severity": verdict.get("severity"), "justification": verdict.get("justification", "")
            })
    payload = {"branch": branch, "base": None, "stat": "", "patch": "", "truncated": False,
               "message": None, "validators": validators}
    try:
        base = resolve_base_branch(project_root)
        with open_repo(project_root) as repo:
            if branch not in repo.heads:
                raise ValueError(f"Task branch '{branch}' is no longer available.")
            payload["base"] = base
            payload["stat"] = repo.git.diff("--stat", f"{base}...{branch}")
            patch = repo.git.diff("--no-ext-diff", f"{base}...{branch}")
        lines = patch.splitlines()
        if max_lines and len(lines) > max_lines:
            payload["patch"] = "\n".join(lines[:max_lines])
            payload["truncated"] = True
        else:
            payload["patch"] = patch
    except Exception as exc:
        payload["message"] = str(exc)
    return payload


def print_task_diff(payload: dict) -> None:
    """Print the diff and the halt record's verdicts for a human reader."""
    print("Changes:")
    print(f"  branch: {payload['branch']}")
    if payload.get("message"):
        print(f"  {payload['message']}")
    else:
        print(f"  base: {payload['base']}")
        print(payload.get("stat") or "  (no changes)")
        if payload.get("patch"):
            print(payload["patch"])
        if payload.get("truncated"):
            print("  (patch truncated; use --max-diff-lines 0 for full patch)")
    print("Validator verdicts:")
    if not payload.get("validators"):
        print("  (none recorded)")
    for validator, verdicts in payload.get("validators", {}).items():
        print(f"  {validator}:")
        for verdict in verdicts:
            print(f"    [{verdict.get('severity')}] {verdict.get('justification', '')}")
