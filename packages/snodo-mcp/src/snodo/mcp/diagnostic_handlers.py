"""Read-only MCP adapters for the CLI project diagnostics."""

from __future__ import annotations

from pathlib import Path


def _error(command: str, message: str) -> dict:
    return {"schema": f"snodo.{command}.v1", "ok": False, "error": message}


class DiagnosticToolHandler:
    """Expose the CLI's machine contracts without adding an MCP shape."""

    def __init__(self, project_root: str) -> None:
        self.project_root = project_root

    def survey(self, arguments: dict) -> dict:
        from snodo.project import get_project_id, scope_for_project_id
        from snodo.survey.analyzer import analyze_repository
        from snodo.survey.drift import compare_protocol
        from snodo.tools.git import open_repo
        from snodo.protocols import load_protocol

        root = Path(self.project_root)
        try:
            with open_repo(str(root)) as repo:
                root = Path(repo.working_dir)
        except Exception:
            return _error("survey", "Not inside a git repository.")
        protocol = None
        protocol_path = root / ".snodo" / "protocol.yml"
        if protocol_path.exists():
            protocol = load_protocol(protocol_path)
            if protocol is None:
                return _error("survey", f"The protocol at {protocol_path} could not be loaded, so there is nothing to compare the code against.")
        # The deterministic analyzer is the default. Agent integration remains
        # opt-in and is supplied by the shared survey judge when configured.
        judge = None
        if arguments.get("agent", False):
            from snodo.survey.judge import build_survey_judge
            judge = build_survey_judge(root, "force")
        analysis = analyze_repository(root, judge=judge, judge_mode="force" if judge else "off")
        if judge is not None and analysis.agent_consulted:
            analysis.agent_model = getattr(judge, "model", None)
        if protocol is not None:
            analysis.drift = compare_protocol(root, analysis, protocol)
        project_id, _ = get_project_id(str(root))
        return {
            "schema": "snodo.survey.v1", "ok": True, "project_root": str(root),
            "project_id": project_id, "scope": scope_for_project_id(project_id),
            "display_name": root.name, "analysis": analysis.to_dict(),
        }

    def intake(self, arguments: dict) -> dict:
        from snodo.protocols import load_protocol
        from snodo.survey.criteria import propose_criteria
        from snodo.tools.git import open_repo

        try:
            with open_repo(self.project_root) as repo:
                root = Path(repo.working_dir)
        except Exception:
            return _error("intake", "Not inside a git repository.")
        protocol_path = root / ".snodo" / "protocol.yml"
        if not protocol_path.exists():
            return _error("intake", "No .snodo/protocol.yml found. Intake adds criteria to a protocol; run 'snodo init' first.")
        protocol = load_protocol(protocol_path)
        if protocol is None:
            return _error("intake", f"The protocol at {protocol_path} could not be loaded.")
        proposals = propose_criteria(root)
        return {
            "schema": "snodo.intake.v1", "ok": True, "project_root": str(root),
            "protocol_id": protocol.protocol_id,
            "proposals": [proposal.to_dict() for proposal in proposals],
            "written": False,
        }

    def ready(self, arguments: dict) -> dict:
        from snodo.infrastructure.paths import resolve_project_root
        from snodo.project import get_project_id, scope_for_project_id
        from snodo.readiness.checker import assess_readiness
        from snodo.protocols import load_protocol

        project_root = resolve_project_root()
        if project_root is None:
            return _error("ready", "Not inside a snodo project.")
        root = Path(project_root)
        protocol_path = Path(arguments.get("protocol", ".snodo/protocol.yml"))
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        protocol = load_protocol(protocol_path)
        if protocol is None:
            return _error("ready", f"Could not load protocol: {protocol_path}")
        mode_filter = arguments.get("mode")
        if mode_filter and mode_filter not in [mode.mode_id for mode in protocol.modes]:
            known = ", ".join(mode.mode_id for mode in protocol.modes)
            return _error("ready", f"Unknown mode '{mode_filter}'. Known modes: {known}")
        assessment = assess_readiness(root, protocol)
        project_id, _ = get_project_id(str(root))
        return {
            "schema": "snodo.ready.v1", "ok": True, "project_root": str(root),
            "mode_filter": mode_filter, "project_id": project_id,
            "scope": scope_for_project_id(project_id), "display_name": root.name,
            "protocol_id": assessment.protocol_id, "score": assessment.score,
            "total_checks": assessment.total_checks, "passed_checks": assessment.passed_checks,
            "repository_findings_count": len(assessment.repository_findings),
            "workstation_findings_count": len(assessment.workstation_findings),
            "findings": [finding.to_dict() for finding in assessment.all_findings],
        }

    def tool_handlers(self) -> dict:
        return {"survey": self.survey, "intake": self.intake, "ready": self.ready}
