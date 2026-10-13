"""Render the concise, read-only Snodo home view."""

import os
from pathlib import Path


def render_home_view(*, color: bool = False) -> str:
    """Return a short project status and next-step view without changing state."""
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    from snodo.cli.commands import load_protocol
    from snodo.infrastructure.paths import resolve_project_root

    root = resolve_project_root()
    if root is None:
        try:
            Repo(str(Path.cwd()), search_parent_directories=True)
        except (InvalidGitRepositoryError, NoSuchPathError):
            lines = ["Snodo · outside a project", "", "Next step:", "  cd into a git repository"]
        else:
            lines = ["Snodo · repository", "", "! Snodo   project not initialized", "", "Next step:", "  snodo init"]
        return _finish(lines, color)

    project_root = Path(root)
    protocol_path = project_root / ".snodo" / "protocol.yml"
    protocol = load_protocol(protocol_path)
    if protocol is None:
        lines = [f"Snodo · {project_root.name}", "", "! protocol   could not load .snodo/protocol.yml", "", "Next step:", "  snodo ready"]
        return _finish(lines, color)

    mode_id = getattr(protocol, "initial_mode", None) or "unknown"
    modes = getattr(protocol, "modes", [])
    mode = next((item for item in modes if getattr(item, "mode_id", None) == mode_id), None)
    coder = getattr(mode, "coder", None) or "configured"
    from snodo.readiness.checker import assess_readiness

    assessment = assess_readiness(project_root, protocol)
    findings = assessment.all_findings
    blocking = any(getattr(f.severity, "value", f.severity) == "blocker" for f in findings)
    lines = [f"Snodo · {project_root.name} · {mode_id}", "", "✓ protocol   .snodo/protocol.yml", f"✓ coder      {coder}"]

    from snodo.cli.commands.cloud_status import sync_issue_notice

    notice = sync_issue_notice()
    if notice:
        lines.append(f"! cloud      {notice.removeprefix('Cloud sync needs attention: ').removesuffix('. Run `snodo cloud sync --all --force`.')}")
        lines.append("             they're catching up in the background · snodo cloud status")
    for finding in findings[: max(0, 3 - (1 if notice else 0))]:
        severity = getattr(finding.severity, "value", finding.severity)
        lines.append(f"{'!' if severity in ('blocker', 'warn') else '✓'} readiness  {finding.description}")

    next_command = "snodo ready" if blocking else 'snodo run "describe the change you want"'
    lines.extend(["", "Next step:", f"  {next_command}"])
    return _finish(lines, color)


def _finish(lines: list[str], color: bool) -> str:
    """Apply ANSI color only when requested and color has not been disabled."""
    text = "\n".join(lines) + "\n"
    if not color or os.environ.get("NO_COLOR") is not None:
        return text
    return text.replace("✓", "\033[32m✓\033[0m").replace("!", "\033[33m!\033[0m")
