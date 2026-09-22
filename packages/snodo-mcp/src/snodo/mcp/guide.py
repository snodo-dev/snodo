"""Source-backed getting-started guidance for the MCP orchestrator."""

from pathlib import Path
import re

from snodo.mcp.tools import TOOL_REGISTRY


_SOURCES = {
    "authoring": "authoring-a-plan.md",
    "automation": "running-unattended.md",
    "machine": "machine-interface.md",
    "runbook": "runbook.md",
}

_TOPICS = {
    "spec": (("authoring", "## 3. What goes in a task spec"),),
    "waves": (("authoring", "## 1. The one modelling rule"),),
    "halts": (
        ("machine", "### `snodo validate <task_spec> [--phase pre_execute|post_execute] [--mode <m>]`"),
        ("machine", "## Exit codes"),
    ),
    "run": (("authoring", "## 5. Running it"),),
    "mistakes": (
        ("authoring", "## 4. What gets the plan refused"),
        ("authoring", "## 7. Checklist for an orchestrator"),
    ),
    "planning": (("authoring", "## 8. The planning loop, end to end"),),
    "automation": (("automation", "# Running Snodo unattended"),),
}

_TOPIC_ALIASES = {
    "authoring": "spec",
    "plan": "waves",
    "sizing": "waves",
    "follow-run": "run",
    "common-mistakes": "mistakes",
    "end-to-end": "planning",
}


def _docs_root(project_root: str) -> Path:
    """Find the checked-in docs without copying their contents into the tool."""
    candidates = [Path(project_root) / "docs"]
    here = Path(__file__).resolve()
    candidates.extend(parent / "docs" for parent in here.parents)
    for candidate in candidates:
        if all((candidate / filename).is_file() for filename in _SOURCES.values()):
            return candidate
    raise RuntimeError("Snodo guide sources are not available")


def _section(path: Path, heading: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line == heading), None)
    if start is None:
        raise RuntimeError(f"Guide section not found: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("#") and len(lines[i]) - len(lines[i].lstrip("#")) <= level:
            end = i
            break
    return "\n".join(lines[start:end]).strip()


def _exposed_only(text: str, exposed: set[str]) -> str:
    """Drop source lines that would instruct this mode to call an absent tool."""
    unavailable = set(TOOL_REGISTRY) - exposed
    return "\n".join(
        line for line in text.splitlines()
        if not any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", line) for name in unavailable)
    ).strip()


def _read_topics(project_root: str, topics: tuple[tuple[str, str], ...], exposed: set[str]) -> str:
    root = _docs_root(project_root)
    chunks = []
    for source, heading in topics:
        text = _exposed_only(_section(root / _SOURCES[source], heading), exposed)
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)


def guide_text(project_root: str, exposed: set[str], topic: str | None = None) -> str:
    """Return the requested guide, derived from the authoritative docs."""
    if topic:
        key = _TOPIC_ALIASES.get(topic.strip().lower(), topic.strip().lower())
        if key not in _TOPICS:
            return "Unknown guide topic. Ask for one of: spec, waves, halts, run, mistakes, planning, automation."
        result = _read_topics(project_root, _TOPICS[key], exposed)
        return result or "That topic has no instructions for the tools exposed in this mode."

    chunks = [
        "# Snodo getting started",
        "Call the guide with `spec`, `waves`, `halts`, `run`, `mistakes`, `planning`, or `automation` for the next topic.",
        "`planning` walks through writing intent, sizing waves and tasks, writing specs, validating, dispatching, following jobs, and reading the outcome.",
        "`automation` covers intent-to-merged-work orchestration for long unattended runs.",
    ]
    if "run_plan" in exposed:
        chunks.append(
            "First use `propose_plan`, add each spec with `generate_spec`, check it with "
            "`validate_plan`, then call `run_plan`. Poll the returned job id with "
            "`get_job_status`; inspect failures with `get_job_logs`."
        )
        chunks.append(_read_topics(project_root, _TOPICS["spec"], exposed))
        chunks.append(_read_topics(project_root, _TOPICS["waves"], exposed))
        chunks.append(_read_topics(project_root, _TOPICS["mistakes"], exposed))
        chunks.append(_read_topics(project_root, _TOPICS["run"], exposed))
    elif "dispatch_task" in exposed:
        chunks.append(
            "Write a standalone spec, call `validate_task`, then `dispatch_task`. "
            "Poll the returned job id with `get_job_status`; inspect failures with "
            "`get_job_logs`."
        )
        chunks.append(_read_topics(project_root, _TOPICS["spec"], exposed))
        chunks.append(_read_topics(project_root, _TOPICS["halts"], exposed))
    else:
        chunks.append(_read_topics(project_root, _TOPICS["halts"], exposed))
    return "\n\n".join(chunk for chunk in chunks if chunk)
