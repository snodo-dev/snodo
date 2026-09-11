"""Survey command — analyze existing repository and propose governance.

FILE: snodo/cli/commands/survey_cmd.py

Survey reads an existing repository and proposes what governing it would mean,
based on observable evidence: languages, service boundaries, test commands,
documentation locations, decision records.

The report distinguishes two kinds of findings:
1. Observable: languages, module boundaries, test commands, docs locations
2. NOT inferred: what the protocol SHOULD demand (absence of a practice is not
   a requirement to drop it from the protocol)

Never writes to the repository. Never infers governance requirements from
absence of current practices. Never clobbers existing protocols.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import typer


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def survey(
        json: bool = typer.Option(
            False, "--json", help="Emit machine-readable JSON",
        ),
    ):
        """Analyze repository and propose what governance would mean."""
        return survey_command(SimpleNamespace(json=json))


def survey_command(args) -> int:
    """Analyze repository and propose governance based on observable evidence."""
    from snodo.cli.json_output import (
        emit_error,
        emit_json,
        schema_name,
        EXIT_INTERNAL_ERROR,
        EXIT_PASS,
    )
    from snodo.project import get_project_id, scope_for_project_id
    from snodo.survey.analyzer import analyze_repository

    json_out = getattr(args, "json", False)

    # Find the git repository root (even if not a snodo project yet)
    project_root = Path.cwd()
    try:
        from git import Repo
        Repo(str(project_root), search_parent_directories=True)
    except Exception:
        if json_out:
            return emit_error("survey", "Not inside a git repository.", EXIT_INTERNAL_ERROR)
        print("Error: Not inside a git repository.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Walk up to find the git root
    try:
        repo = Repo(str(project_root), search_parent_directories=True)
        project_root = Path(repo.working_dir)
    except Exception:
        if json_out:
            return emit_error("survey", "Could not find git repository root.", EXIT_INTERNAL_ERROR)
        print("Error: Could not find git repository root.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Check if a protocol already exists
    protocol_path = project_root / ".snodo" / "protocol.yml"
    if protocol_path.exists():
        if not json_out:
            print(
                f"This repository already has a protocol at {protocol_path}.",
                file=sys.stderr,
            )
            print("Survey proposes governance for repositories without a declared protocol.", file=sys.stderr)
        if json_out:
            return emit_error(
                "survey",
                f"Protocol already exists at {protocol_path}",
                EXIT_INTERNAL_ERROR,
            )
        return EXIT_INTERNAL_ERROR

    # Analyze the repository
    try:
        analysis = analyze_repository(project_root)
    except Exception as e:
        if json_out:
            return emit_error("survey", f"Analysis failed: {e}", EXIT_INTERNAL_ERROR)
        print(f"Error: Analysis failed: {e}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Resolve project identity
    project_id, _ = get_project_id(str(project_root))
    scope = scope_for_project_id(project_id)
    display_name = project_root.name

    if json_out:
        return emit_json(
            {
                "schema": schema_name("survey"),
                "ok": True,
                "project_root": str(project_root),
                "project_id": project_id,
                "scope": scope,
                "display_name": display_name,
                "analysis": analysis.to_dict(),
            },
            EXIT_PASS,
        )

    # Human-readable output
    print(f"Repository Analysis: {display_name}")
    print(f"Project ID: {project_id} ({scope})")
    print()

    # Display module findings
    print("Discovered Modules:")
    if analysis.modules:
        for module in analysis.modules:
            print(f"  • {module.module_id}")
            if module.paths:
                print(f"    Paths: {', '.join(module.paths)}")
            if module.languages:
                print(f"    Languages: {', '.join(module.languages)}")
            if module.test_command:
                print(f"    Test: {module.test_command}")
            if module.decisions_path:
                print(f"    Decisions: {module.decisions_path}")
            if module.evidence:
                for ev in module.evidence:
                    print(f"      [evidence] {ev}")
    else:
        print("  No distinct modules detected. Repository appears to be a single package.")
    print()

    # Display tooling findings
    print("Repository Tooling:")
    if analysis.repository_tooling:
        for key, value in analysis.repository_tooling.items():
            print(f"  • {key}: {value}")
    else:
        print("  No repository-level tooling detected.")
    print()

    # Display decision records findings
    print("Decision Records:")
    if analysis.decision_paths:
        for path in analysis.decision_paths:
            print(f"  • {path}")
    else:
        print("  No decision records directory found (docs/decisions or docs/adr).")
    print()

    # Display test command findings
    print("Test Command Resolution:")
    if analysis.test_command:
        print(f"  Detected: {analysis.test_command}")
        if analysis.test_marker_file:
            print(f"  From: {analysis.test_marker_file} (marker file in git)")
    else:
        print("  No test command could be resolved from marker files or explicit configuration.")
    print()

    # Display observable findings
    print("Observable Findings:")
    if analysis.findings:
        for finding in analysis.findings:
            print(f"  • {finding.message}")
            if finding.evidence:
                for ev in finding.evidence:
                    print(f"      {ev}")
    else:
        print("  No significant findings.")
    print()

    print("Next Steps:")
    print("  1. Review this analysis — edit modules, tooling, and paths as needed")
    print("  2. Create governance records for architectural decisions")
    print("  3. Run: snodo init --template=<choice> to set up your protocol")
    print("  4. Run: snodo ready to assess readiness")

    return EXIT_PASS
