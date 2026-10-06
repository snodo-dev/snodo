"""Validate the changes represented by a git ref range or hosted request."""

import contextlib
import io
import json
from pathlib import Path


def validate_changes(*, task_spec, phase, protocol, mode, json_output, base, head, change_request):
    """Resolve changed content, then pass it through the existing validate path."""
    from snodo.cli.commands.validate_cmd import validate_command
    from snodo.infrastructure.paths import resolve_project_root
    from snodo.cli.json_output import emit_error, EXIT_INTERNAL_ERROR
    from snodo.cli.commands import load_protocol

    root = resolve_project_root()
    if root is None:
        return emit_error("validate", "Not inside a snodo project.", EXIT_INTERNAL_ERROR)
    if (base is None) != (head is None) or (change_request is not None and base is not None):
        return emit_error("validate", "Provide both --base and --head, or use --pr.", EXIT_INTERNAL_ERROR)
    try:
        if change_request is not None:
            protocol_obj = load_protocol(Path(root) / protocol)
            if protocol_obj is None:
                raise ValueError("Could not load protocol")
            from snodo.providers.registry import detect_provider
            provider = detect_provider(root, protocol_obj.metadata)
            base, head = provider.resolve_change_request_refs(str(change_request))
        if not base:
            raise ValueError("A base/head ref range or --pr is required")
        from git import Repo
        repository = Repo(root)
        try:
            base_commit = repository.commit(base).hexsha
            head_commit = repository.commit(head).hexsha
            diff = repository.git.diff(
                "--no-ext-diff", "--no-renames", f"{base_commit}...{head_commit}", "--",
            )
        finally:
            repository.close()
        if not diff.strip():
            raise ValueError(f"No changes found between {base} and {head}")
    except Exception as exc:  # noqa: BLE001
        return emit_error("validate", str(exc), EXIT_INTERNAL_ERROR)

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = validate_command(type("Args", (), {
        "task_spec": task_spec or f"Judge the changes in {base}...{head}:\n\n{diff}",
        "phase": phase, "protocol": protocol, "mode": mode, "json": json_output,
        })())
    if json_output:
        try:
            payload = json.loads(captured.getvalue())
            if payload.get("status") == "blocker":
                payload["halt"] = {
                    "status": "blocker", "task_id": payload.get("task_id"),
                    "phase": payload.get("phase"), "mode": payload.get("mode"),
                    "validator_results": payload.get("results", []),
                    "policy_decision": payload.get("policy_decision"),
                }
            from snodo.cli.json_output import emit_json
            return emit_json(payload, result)
        except (ValueError, TypeError):
            pass
    print(captured.getvalue(), end="")
    return result
