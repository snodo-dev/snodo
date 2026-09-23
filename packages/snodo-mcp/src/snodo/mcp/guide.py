"""Source-backed getting-started guidance for the MCP orchestrator."""

from pathlib import Path
import re

from snodo.mcp.tools import TOOL_REGISTRY


_GUIDE_MARKER = re.compile(r"<!--\s*snodo-guide\s+(.+?)\s*-->")
_ATTRIBUTE = re.compile(r'([a-z]+)="([^"]*)"')
_BUNDLED_DOCS = Path(__file__).with_name("guide_docs")


def _doc_roots(project_root: str) -> tuple[Path, ...]:
    here = Path(__file__).resolve()
    checkout_docs = tuple(parent / "docs" for parent in here.parents)
    return (Path(project_root) / "docs", _BUNDLED_DOCS, *checkout_docs)


def _topic_registry(project_root: str) -> dict[str, dict]:
    """Read topic declarations from the guide markers in the source documents."""
    candidates = _doc_roots(project_root)
    root = next((
        candidate for candidate in candidates
        if candidate.is_dir() and any(_GUIDE_MARKER.search(path.read_text(encoding="utf-8"))
                                      for path in candidate.glob("*.md"))
    ), None)
    if root is None:
        raise RuntimeError("Snodo guide sources are not available")

    topics: dict[str, dict] = {}
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in _GUIDE_MARKER.finditer(text):
            attributes = dict(_ATTRIBUTE.findall(match.group(1)))
            required = {"topic", "summary", "section"}
            if not required <= attributes.keys():
                raise RuntimeError(f"Invalid Snodo guide marker in {path}")
            topic = topics.setdefault(attributes["topic"], {
                "summary": attributes["summary"], "sections": [], "aliases": [],
            })
            if topic["summary"] != attributes["summary"]:
                raise RuntimeError(f"Inconsistent summary for guide topic {attributes['topic']}")
            topic["sections"].append((path.name, attributes["section"]))
            topic["aliases"].extend(
                alias for alias in attributes.get("aliases", "").split(",") if alias
            )
    if not topics:
        raise RuntimeError("No Snodo guide topics are registered")
    return topics


def guide_topics(project_root: str) -> dict[str, dict]:
    """Expose registered names, summaries, and aliases to other MCP surfaces."""
    return _topic_registry(project_root)


def _docs_root(project_root: str) -> Path:
    for candidate in _doc_roots(project_root):
        if candidate.is_dir() and any(
            _GUIDE_MARKER.search(path.read_text(encoding="utf-8"))
            for path in candidate.glob("*.md")
        ):
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


def _contains_unavailable_tool(text: str, unavailable: set[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) for name in unavailable)


def _exposed_only(text: str, exposed: set[str]) -> str:
    """Drop whole prose paragraphs or individual list items mentioning hidden tools."""
    unavailable = set(TOOL_REGISTRY) - exposed
    output: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        if not lines:
            continue
        if re.match(r"\s*(?:[-*+] |\d+[.)] )", lines[0]):
            kept: list[str] = []
            item: list[str] = []
            for line in lines:
                if re.match(r"\s*(?:[-*+] |\d+[.)] )", line):
                    if item and not _contains_unavailable_tool("\n".join(item), unavailable):
                        kept.extend(item)
                    item = [line]
                elif item:
                    item.append(line)
                elif not _contains_unavailable_tool(line, unavailable):
                    kept.append(line)
            if item and not _contains_unavailable_tool("\n".join(item), unavailable):
                kept.extend(item)
            if kept:
                output.append("\n".join(kept))
        elif not _contains_unavailable_tool(block, unavailable):
            output.append(block)
    return "\n\n".join(output).strip()


def _read_topic(project_root: str, topic: dict, exposed: set[str]) -> str:
    root = _docs_root(project_root)
    chunks = [
        _exposed_only(_section(root / filename, heading), exposed)
        for filename, heading in topic["sections"]
    ]
    return "\n\n".join(chunk for chunk in chunks if chunk)


def guide_menu(project_root: str) -> str:
    """One concise menu line per registered topic, suitable for descriptions."""
    return "\n".join(
        f"- `{name}` — {topic['summary']}"
        for name, topic in _topic_registry(project_root).items()
    )


def guide_text(project_root: str, exposed: set[str], topic: str | None = None) -> str:
    """Return the requested guide, derived from the authoritative docs."""
    topics = _topic_registry(project_root)
    aliases = {alias: name for name, data in topics.items() for alias in data["aliases"]}
    if topic:
        key = topic.strip().lower()
        key = aliases.get(key, key)
        if key not in topics:
            return "Unknown guide topic. Ask for one of: " + ", ".join(topics) + "."
        result = _read_topic(project_root, topics[key], exposed)
        return result or "That topic has no instructions for the tools exposed in this mode."

    menu = guide_menu(project_root)
    if "run_plan" in exposed:
        path = "First use `propose_plan`, add task specs with `generate_spec`, then `validate_plan` and `run_plan`; poll with `get_job_status` and inspect failures with `get_job_logs`."
    elif "dispatch_task" in exposed:
        path = "Write a standalone spec, call `validate_task`, then `dispatch_task`; poll with `get_job_status` and inspect failures with `get_job_logs`."
    else:
        path = "Use the tools available in this mode for its declared purpose."
    return f"# Snodo getting started\n{path}\n\nGuide topics:\n{menu}"
