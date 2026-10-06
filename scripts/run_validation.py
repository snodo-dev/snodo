"""Run Snodo validation and publish its structured verdict to GitHub Actions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    protocol = os.environ.get("INPUT_PROTOCOL", ".snodo/protocol.yml")
    mode = os.environ.get("INPUT_MODE", "")
    model = os.environ.get("INPUT_MODEL", "")
    api_key = os.environ.get("INPUT_KEY", "")
    key_env = os.environ.get("INPUT_KEY_ENV", "OPENAI_API_KEY")
    pr_number = os.environ.get("INPUT_PR", "") or os.environ.get("GITHUB_EVENT_NUMBER", "")

    if api_key:
        os.environ[key_env] = api_key
    if model:
        os.environ["SNODO_MODEL"] = model
    if os.environ.get("INPUT_GITHUB_TOKEN"):
        os.environ["GITHUB_TOKEN"] = os.environ["INPUT_GITHUB_TOKEN"]

    # Fixed executable/argument structure; input values are separate argv items.
    ready = subprocess.run(  # noqa: S603
        ["snodo", "ready", "--protocol", protocol, "--json"],  # noqa: S607
        text=True,
        capture_output=True,
        check=False,
    )
    if ready.stdout:
        print(ready.stdout, end="")
    if ready.stderr:
        print(ready.stderr, end="", file=sys.stderr)
    if ready.returncode:
        return ready.returncode

    if not pr_number:
        print("::error::Pull request number is required (use this action on pull_request).")
        return 4
    command = ["snodo", "validate", "--protocol", protocol, "--pr", pr_number, "--json"]
    if mode:
        command.extend(["--mode", mode])
    result = subprocess.run(command, text=True, capture_output=True, check=False)  # noqa: S603
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = {"status": "internal_error", "results": [], "error": result.stderr}
    verdict = str(payload.get("status", "internal_error"))
    _set_output("verdict", verdict)
    _set_output("status", str(result.returncode))
    _write_summary(payload, result.returncode)
    return result.returncode


def _set_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def _write_summary(payload: dict, code: int) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    verdict = payload.get("status", "internal_error")
    lines = ["## Snodo protocol validation", "", f"**Verdict:** `{verdict}`", ""]
    if payload.get("error"):
        lines.extend([f"**Error:** {payload['error']}", ""])
    results = payload.get("results") or []
    if results:
        lines.extend(["| Validator | Severity | Justification |", "|---|---|---|"])
        for item in results:
            validator = str(item.get("validator_id", "" )).replace("|", "\\|")
            severity = str(item.get("severity", "")).replace("|", "\\|")
            justification = str(item.get("justification", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {validator} | {severity} | {justification} |")
    lines.extend(["", f"Validation exit code: `{code}`", ""])
    with Path(summary).open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
