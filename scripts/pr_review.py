#!/usr/bin/env python3
"""Generate one synthesized, multi-agent review comment for a GitHub pull request.

The script writes Markdown to a file; it never posts to GitHub. In preview and
test mode, ``--dry-run-results`` accepts a JSON list of recon result objects.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

OUTCOMES = ("MERGED", "MINOR REWORK", "MAJOR REWORK", "REJECTED")
DEFAULT_DIFF_LIMIT = 100_000
RECON_TIMEOUT_SECONDS = 300


def _gh_json(repo: str, pr: int, field: str) -> str:
    completed = subprocess.run(  # noqa: S603, S607 - executable and arguments are fixed below
        ["gh", "pr", "view", str(pr), "--repo", repo, "--json", field],  # noqa: S607 - gh is the fixed executable
        check=True, capture_output=True, text=True,
    )
    return str(json.loads(completed.stdout).get(field) or "")


def fetch_pull_request(repo: str, pr: int) -> tuple[str, str, str]:
    title = _gh_json(repo, pr, "title")
    body = _gh_json(repo, pr, "body")
    diff = subprocess.run(  # noqa: S603, S607 - executable and arguments are fixed below
        ["gh", "pr", "diff", str(pr), "--repo", repo],  # noqa: S607 - gh is the fixed executable
        check=True, capture_output=True, text=True,
    ).stdout
    return title, body, diff


def build_question(title: str, body: str, diff: str, limit: int) -> tuple[str, bool]:
    truncated = len(diff) > limit
    visible_diff = diff[:limit]
    notice = "\n[DIFF TRUNCATED: review only the available prefix; the full diff exceeded the configured limit.]\n" if truncated else ""
    question = f"""Review this pull request independently and critically.

Repository standards: consult relevant docs/decisions ADRs; check the closed state machine, import layering, test gates, and changelog gates. Give findings with file and line evidence, recommend exactly one outcome label (MERGED, MINOR REWORK, MAJOR REWORK, REJECTED), and explicitly state what you could not verify. Do not infer facts not present in the repository or diff.

PR title: {title}
PR body:
{body}

Diff:{notice}
```diff
{visible_diff}
```
"""
    return question, truncated


def _load_recon_models() -> list[str]:
    import yaml
    from snodo.infrastructure.config import resolve_home

    path = resolve_home() / "config.yml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    recon = data.get("llm", {}).get("recon", {}) if isinstance(data, dict) else {}
    models = recon.get("models", []) if isinstance(recon, dict) else []
    return [str(model) for model in models]


def run_recon(question: str, repo: str, pr: int) -> list[dict[str, Any]]:
    """Run three configured agents through the audited recon lifecycle."""
    from snodo.recon import ReconManager, resolve_recon_agents

    models = _load_recon_models()
    lanes = resolve_recon_agents(requested_n=3, recon_models=models)
    root = Path.cwd()
    manager = ReconManager(str(root))
    recon_id = manager.submit(question, ["."], agents=lanes)
    deadline = time.monotonic() + RECON_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = manager.get_status(recon_id)
        if status.get("status") in {"complete", "failed"}:
            break
        time.sleep(0.1)
    else:
        return [
            {"agent": f"agent-{index}", "result": "", "error": "recon timed out"}
            for index in range(1, len(lanes) + 1)
        ]

    raw = manager.get_results(recon_id)
    results = [_result_dict(item) for item in raw.get("results", [])]
    if raw.get("status") == "failed" and not results:
        return [
            {"agent": f"agent-{index}", "result": "", "error": "recon failed"}
            for index in range(1, len(lanes) + 1)
        ]
    return results


def _result_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return {"agent": str(getattr(item, "agent", "agent")), "result": str(getattr(item, "result", "")), "error": getattr(item, "error", None)}


def _synthesize(results: list[dict[str, Any]]) -> str:
    import litellm

    models = _load_recon_models()
    if not models:
        raise RuntimeError("No model configured in llm.recon.models for synthesis")
    prompt = f"""Synthesize these independent PR review results. Return only JSON with keys: outcome (exactly one of {', '.join(OUTCOMES)}), summary (one sentence naming returned and failed agents), rows (array of objects with kind exactly Agreement, Disagreement, or Misalignment; finding; agents (mapping agent name to finding/status); status), and rework (array of short suggested actions). Include only findings supported by agent results. Results: {json.dumps(results, ensure_ascii=False)}"""
    # Resolve the model the way recon does, so custom providers (ollama-cloud,
    # ocgo) route through their configured endpoint and credentials.
    from snodo.config import ConfigManager

    model = models[0]
    kwargs: dict[str, Any] = {
        "model": ConfigManager.resolve_litellm_model(model),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    api_key = ConfigManager().get_key_for_model(model)
    if api_key:
        kwargs["api_key"] = api_key
    api_base = ConfigManager.resolve_api_base(model)
    if api_base:
        kwargs["api_base"] = api_base
    extra_headers = ConfigManager.resolve_extra_headers(model, task_id="pr-review")
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    response = litellm.completion(**kwargs)
    return str(response.choices[0].message.content or "")


def _parse_synthesis(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("synthesis did not return JSON")
    data = json.loads(match.group())
    if data.get("outcome") not in OUTCOMES:
        raise ValueError("synthesis outcome must be one of: " + ", ".join(OUTCOMES))
    return data


def synthesize(results: list[dict[str, Any]]) -> dict[str, Any]:
    for attempt in range(2):
        text = _synthesize(results) if attempt == 0 else _synthesize(results + [{"instruction": f"Previous outcome invalid; outcome must be exactly one of {OUTCOMES}."}])
        try:
            return _parse_synthesis(text)
        except (ValueError, json.JSONDecodeError):
            if attempt:
                raise
    raise AssertionError("unreachable")


def render_comment(results: list[dict[str, Any]], synthesis: dict[str, Any], truncated: bool = False) -> str:
    returned = [r for r in results if str(r.get("result", "")).strip() and not r.get("error")]
    failed = [r for r in results if r not in returned]
    columns = [str(r.get("agent") or f"agent-{i}") for i, r in enumerate(returned, 1)]
    lines = [f"## Suggested outcome: {synthesis['outcome']}", "", str(synthesis.get("summary", ""))]
    if failed:
        lines[-1] += " Failed or empty: " + ", ".join(str(r.get("agent", "agent")) for r in failed) + "."
    if truncated:
        lines.extend(["", "**Notice:** The PR diff was truncated to the configured size limit; reviewers did not receive its full contents."])
    lines.extend(["", "| Kind | Finding | " + " | ".join(columns) + " | Status |", "| --- | --- | " + " | ".join(["---"] * len(columns)) + " | --- |"])
    for row in synthesis.get("rows", []):
        agents = row.get("agents", {})
        cells = [str(agents.get(agent, "—")).replace("|", "\\|").replace("\n", " ") for agent in columns]
        cells = [str(row.get("kind", "")), str(row.get("finding", "")), *cells, str(row.get("status", ""))]
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    rework = synthesis.get("rework", [])
    lines.extend(["", "### Suggested rework"])
    lines.extend([f"- {item}" for item in rework] or ["- None suggested."])
    for i, result in enumerate(results, 1):
        name = str(result.get("agent") or f"agent-{i}")
        detail = str(result.get("result") or "")
        if result.get("error"):
            detail = f"Error: {result['error']}\n\n{detail}".strip()
        if not detail:
            detail = "No result returned."
        lines.extend(["", f"<details><summary>{name}</summary>", "", detail, "", "</details>"])
    return "\n".join(lines) + "\n"


def render_fix_forward_issue(
    repo: str, pr: int, title: str, review_url: str, synthesis: dict[str, Any]
) -> str:
    """Render an intent-first fix-forward ticket from a non-merged review."""
    lines = [
        f"# Fix forward for pull request #{pr}",
        "",
        f"Pull request: [{title}](https://github.com/{repo}/pull/{pr})",
        f"Review: [automated review comment]({review_url})",
        "",
        f"## Review outcome: {synthesis['outcome']}",
        "",
        "## Symptoms and evidence",
    ]
    rows = synthesis.get("rows", [])
    if rows:
        for row in rows:
            lines.extend([
                f"### {row.get('kind', 'Finding')}: {row.get('finding', '')}",
                f"Status: {row.get('status', '')}",
            ])
            for agent, evidence in (row.get("agents") or {}).items():
                lines.append(f"- {agent}: {evidence}")
            lines.append("")
    else:
        lines.extend(["No finding rows were returned.", ""])
    lines.extend(["## Review summary", "", str(synthesis.get("summary", "")), "", "## Suggested rework (indications only)"])
    lines.extend([f"- {item}" for item in synthesis.get("rework", [])] or ["- None suggested."])
    lines.extend([
        "",
        "## For the coder",
        "",
        "This issue is the tracking issue for the fix-forward work. Include `Fixes #<this issue's number>` in the commit subject.",
        "",
    ])
    return "\n".join(lines)


def _redact_environment(text: str) -> str:
    # Prevent any configured credential accidentally echoed by a provider/model.
    secrets = {value for key, value in os.environ.items() if value and ("KEY" in key.upper() or "TOKEN" in key.upper() or "SECRET" in key.upper())}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--issue-body", type=Path, help="write a fix-forward issue body for non-MERGED outcomes")
    parser.add_argument("--dry-run-results", type=Path)
    parser.add_argument("--diff-limit", type=int, default=int(os.environ.get("SNODO_PR_REVIEW_DIFF_LIMIT", DEFAULT_DIFF_LIMIT)))
    args = parser.parse_args(argv)
    try:
        truncated = False
        canned_synthesis = None
        if args.dry_run_results:
            results = json.loads(args.dry_run_results.read_text())
            if isinstance(results, dict):
                canned_synthesis = results.get("synthesis")
                results = results.get("results", [])
            # Dry runs still exercise truncation rendering when supplied a diff
            # field, without requiring GitHub access.
            truncated = any(bool(r.get("truncated")) for r in results if isinstance(r, dict))
        else:
            title, body, diff = fetch_pull_request(args.repo, args.pr)
            question, truncated = build_question(title, body, diff, args.diff_limit)
            results = run_recon(question, args.repo, args.pr)
        results = [_result_dict(r) for r in results]
        successful = [r for r in results if str(r.get("result", "")).strip() and not r.get("error")]
        if len(successful) < 2:
            for r in results:
                print(_redact_environment(f"{r.get('agent')}: {r.get('error') or 'empty result'}"), file=sys.stderr)
            raise RuntimeError("fewer than two recon agents returned a non-empty result")
        # Canned-result previews are strictly offline. A fixture may include a
        # synthesis object for faithful rendering; otherwise use a neutral,
        # explicit preview summary rather than making a provider call.
        synthesis = canned_synthesis or ({
            "outcome": "MINOR REWORK",
            "summary": f"{len(successful)} of {len(results)} reviewers returned; offline preview uses a provisional outcome.",
            "rows": [],
            "rework": [],
        } if args.dry_run_results else synthesize(results))
        if synthesis.get("outcome") not in OUTCOMES:
            raise ValueError("canned synthesis outcome must be one of: " + ", ".join(OUTCOMES))
        output = _redact_environment(render_comment(results, synthesis, truncated))
        args.out.write_text(output)
        if args.issue_body and synthesis["outcome"] != "MERGED":
            title = _gh_json(args.repo, args.pr, "title") if not args.dry_run_results else f"Pull request #{args.pr}"
            issue_body = render_fix_forward_issue(args.repo, args.pr, title, "{{REVIEW_COMMENT_URL}}", synthesis)
            args.issue_body.write_text(_redact_environment(issue_body))
        return 0
    except Exception as exc:  # user-facing errors must not include subprocess credentials
        print(_redact_environment(f"error: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
