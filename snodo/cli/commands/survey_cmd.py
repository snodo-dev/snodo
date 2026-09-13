"""Survey command — analyze existing repository and propose governance.

FILE: snodo/cli/commands/survey_cmd.py

Survey reads an existing repository and reports what governance means for it,
based on observable evidence: languages, service boundaries, test commands,
documentation locations, decision records.

Two closing questions, depending on what is already there:

* No protocol yet: what would governing this repository mean? The report
  distinguishes two kinds of finding — observable (languages, module
  boundaries, test commands, docs locations) and NOT inferred (what a protocol
  SHOULD demand; absence of a practice is not a requirement to drop it from the
  protocol).
* A protocol already exists: does that protocol still describe this code? The
  same analysis runs, and its result is compared with what the protocol
  declares — where the two agree, and where they have diverged (see
  snodo.survey.drift). Agreement is reported as plainly as divergence, and
  every comparison the protocol's shape does not support is listed as not made
  with its reason.

Two boundary questions are not arithmetic and are put to the configured agent
as classifications over evidence the deterministic pass already gathered —
is a manifest-backed work product or scaffolding, is a source-dense manifest-
less directory an undeclared boundary. The call goes out through the recon
machinery's read-only agent dispatch; the agent is asked to judge the dossier,
not to explore. Verdicts are accepted only when they cite evidence files from
the dossier (attribution is enforced in the analyzer).

Survey works without an agent: with no model configured, with the call
failing, or on --no-agent, the deterministic result stands and every
judgement that was not made is listed in the output.

Never writes to the repository — not the protocol, not a proposal file, not a
suggested diff, and it offers no reconciliation: drift is reported to a person
who decides. Never infers governance requirements from absence of practices.
Never treats a protocol's ungoverned code as code that needs a validator.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import typer


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def survey(
        json: bool = typer.Option(
            False, "--json", help="Emit machine-readable JSON",
        ),
        agent: Optional[bool] = typer.Option(
            None,
            "--agent/--no-agent",
            help=(
                "Consult the configured agent for boundary judgements. "
                "Default: consult one when a provider key is configured."
            ),
        ),
    ):
        """Analyze repository: propose governance, or report protocol drift."""
        return survey_command(
            SimpleNamespace(json=json, agent=agent if agent is not None else "auto")
        )


# ---------------------------------------------------------------------------
# The judge: survey's route to the configured agent, via the recon machinery
# ---------------------------------------------------------------------------

_JUDGE_MAX_TURNS = 3  # classify the dossier; exploring the repo is not the job


def _agent_is_configured(mgr, model: str) -> bool:
    """True when *model*'s provider has a key in snodo's own config.

    Ambient environment keys do not count: this mirrors "a configured agent",
    the thing the operator set up, so survey's auto mode stays predictable.
    """
    try:
        return bool(mgr.get_key_for_model(model))
    except Exception:
        return False


def build_survey_judge(project_root: Path, agent_mode):
    """Build a judge callable for the analyzer, or None when no agent is available.

    The agent is resolved the same way recon resolves one — the configured
    recon models first, the default model otherwise — and the request goes
    out through the recon machinery's read-only agent call.  The judge
    returns None-verdicts itself when the call fails; the analyzer then
    records every judgement as not made.  ``judge.model`` says which agent
    was asked.
    """
    if agent_mode == "off":
        return None

    from snodo.config import ConfigManager
    from snodo.recon import resolve_agent_model, resolve_recon_agents

    mgr = ConfigManager()
    recon_cfg = mgr.load().get("llm", {}).get("recon", {})
    agents = resolve_recon_agents(
        recon_models=recon_cfg.get("models", []),
        recon_default_n=recon_cfg.get("num_agents", 1),
    )
    model = resolve_agent_model(agents[0])
    if agent_mode != "force" and not _agent_is_configured(mgr, model):
        return None

    def judge(dossier: Dict[str, Any]):
        from snodo.recon import call_agent

        prompt = _judgement_prompt(dossier)
        result = call_agent(
            str(project_root),
            model,
            prompt,
            paths=["./"],
            agent_label="survey-judge",
            max_turns=_JUDGE_MAX_TURNS,
        )
        if getattr(result, "error", None):
            return {"reason": f"the agent call failed: {result.error}"}
        verdicts = _parse_verdicts(getattr(result, "result", "") or "")
        if verdicts is None:
            return {"reason": "the agent returned no parseable judgement verdicts"}
        return {"verdicts": verdicts}

    judge.model = model  # type: ignore[attr-defined]
    return judge


def _judgement_prompt(dossier: Dict[str, Any]) -> str:
    """Frame the request as classification over gathered evidence."""
    return (
        "You are the judgement step of `snodo survey`, a read-only repository\n"
        "diagnosis tool. A deterministic pass has already walked the filesystem\n"
        "and gathered the evidence below. Your task is to CLASSIFY, not to\n"
        "explore: decide only the listed questions, from only the evidence\n"
        "given. Do not add requirements, judgments of quality, or subjects.\n"
        "\n"
        "Evidence dossier (JSON):\n"
        + json.dumps(dossier, indent=2, default=str)
        + "\n\n"
        "For every subject, answer with a verdict:\n"
        "- kind \"boundary-role\": one of \"product\", \"scaffolding\", \"abstain\"\n"
        "- kind \"undeclared-boundary\": one of \"module\", \"not-module\", \"abstain\"\n"
        "\n"
        'Reply with exactly one JSON object and nothing else:\n'
        '{"judgements": [{"subject": "<subject id, verbatim>", "verdict": "...", '
        '"reason": "...", "cited_files": ["<path>", ...]}]}\n'
        "\n"
        "Rules:\n"
        "1. \"cited_files\" must be files inside the subject, stated as given in\n"
        "   the evidence or subject-relative (for subject \"app\", \"src/index.ts\"\n"
        "   means \"app/src/index.ts\"). Every citation is checked against the\n"
        "   filesystem; one that cannot be found there discards the verdict.\n"
        "2. Cite at least one file for every verdict.\n"
        "3. When the evidence is not enough to decide, answer \"abstain\" with a\n"
        "   reason. Abstaining honestly beats guessing.\n"
        "4. \"scaffolding\" means the work has no purpose beyond the product: it\n"
        "   exists to document, test, or operate it (a doc site, a test harness,\n"
        "   tooling). A console, admin app, or API that is itself deployed and\n"
        "   used is part of the product, even if its name suggests otherwise.\n"
        "   Decide from what the cited files show, not from the directory's name.\n"
    )


def _parse_verdicts(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract the verdict list from the agent's reply, or None."""
    payload = _extract_json_object(text)
    if not isinstance(payload, dict):
        return None
    judgements = payload.get("judgements")
    if not isinstance(judgements, list):
        return None
    return [item for item in judgements if isinstance(item, dict)]


def _extract_json_object(text: str) -> Optional[dict]:
    """Return the first complete balanced JSON object in *text*."""
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text[idx:])
        except ValueError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    return None


def survey_command(args) -> int:
    """Analyze a repository: propose governance, or compare the protocol to the code."""
    from snodo.cli.json_output import (
        emit_error,
        emit_json,
        schema_name,
        EXIT_INTERNAL_ERROR,
        EXIT_PASS,
    )
    from snodo.project import get_project_id, scope_for_project_id
    from snodo.survey.analyzer import analyze_repository
    from snodo.survey.drift import compare_protocol

    json_out = getattr(args, "json", False)
    agent_mode = getattr(args, "agent", None)

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

    # A protocol already in place changes the closing question, not the
    # analysis: the report compares what the protocol claims with what the code
    # shows. A protocol that exists but cannot be loaded is the one governed
    # case that is a genuine failure — there is nothing to compare against.
    protocol_path = project_root / ".snodo" / "protocol.yml"
    protocol = None
    if protocol_path.exists():
        from snodo.protocols import load_protocol

        protocol = load_protocol(protocol_path)
        if protocol is None:
            message = (
                f"The protocol at {protocol_path} could not be loaded, so there "
                "is nothing to compare the code against."
            )
            if json_out:
                return emit_error("survey", message, EXIT_INTERNAL_ERROR)
            print(f"Error: {message}", file=sys.stderr)
            return EXIT_INTERNAL_ERROR

    # Boundary judgements go to the configured agent when one is reachable;
    # without one the deterministic pass stands and the gaps are reported.
    if agent_mode is False or agent_mode == "off":
        judge_mode = "off"
    elif agent_mode is True or agent_mode == "force":
        judge_mode = "force"
    else:
        judge_mode = "auto"
    judge = build_survey_judge(project_root, judge_mode)

    # Analyze the repository
    try:
        analysis = analyze_repository(
            project_root, judge=judge, judge_mode=judge_mode
        )
    except Exception as e:
        if json_out:
            return emit_error("survey", f"Analysis failed: {e}", EXIT_INTERNAL_ERROR)
        print(f"Error: Analysis failed: {e}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if judge is not None and analysis.agent_consulted:
        analysis.agent_model = getattr(judge, "model", None)

    if protocol is not None:
        analysis.drift = compare_protocol(project_root, analysis, protocol)

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
    if analysis.drift is not None:
        print(
            f"Protocol: .snodo/protocol.yml ({analysis.drift.protocol_id}) — "
            "this repository is governed; the closing question is whether the "
            "protocol still describes the code."
        )
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
            if module.origin != "manifest":
                print(f"    Origin: {module.origin}")
            if module.evidence:
                for ev in module.evidence:
                    print(f"      [evidence] {ev}")
    else:
        print("  No distinct modules detected. Repository appears to be a single package.")
    print()

    # Display agent judgements: each attributable to the cited evidence
    print("Boundary Judgements:")
    if analysis.judgements:
        model_note = f" by agent {analysis.agent_model}" if analysis.agent_model else ""
        print(f"  Consulted{model_note}:")
        for record in analysis.judgements:
            print(
                f"  • {record.subject_path} ({record.kind}): {record.verdict}"
                f" — {record.reason}"
            )
            for cite in record.cited_files:
                print(f"      [cited] {cite}")
    elif analysis.agent_consulted:
        print("  The agent was consulted but no verdict was accepted.")
    else:
        print("  No agent consulted; deterministic result stands.")
    if analysis.unmade_judgements:
        print("  Not made (deterministic result kept for each):")
        for gap in analysis.unmade_judgements:
            print(f"  • {gap.subject_path} ({gap.kind}): {gap.reason}")
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

    # Display test command findings. The summary speaks for the repository
    # root only: a monorepo can confirm a command on every module and still
    # have no repository-level command, and saying so about the wrong scope
    # reads as "this project has no tests".
    print("Test Command Resolution:")
    if analysis.test_command:
        print(f"  Repository-level: {analysis.test_command}")
        if analysis.test_marker_file:
            print(f"  From: {analysis.test_marker_file} (command confirmed in that file)")
    else:
        print(
            "  No repository-level test command could be resolved from marker "
            "files or explicit configuration."
        )
        modules_with_commands = [m for m in analysis.modules if m.test_command]
        if modules_with_commands:
            print(
                f"  Module-level test commands were confirmed for "
                f"{len(modules_with_commands)} module(s); see Discovered Modules."
            )
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

    # Display the protocol-versus-code relationship, when there is a protocol
    if analysis.drift is not None:
        _print_drift(analysis.drift)

    if analysis.drift is None:
        print("Next Steps:")
        print("  1. Review this analysis — edit modules, tooling, and paths as needed")
        print("  2. Create governance records for architectural decisions")
        print("  3. Run: snodo init --template=<choice> to set up your protocol")
        print("  4. Run: snodo ready to assess readiness")
    else:
        print("Next Steps:")
        print("  1. Read the comparisons above against the repository itself")
        print("  2. Where they diverge, decide which side moved — the code, or the")
        print("     protocol's claim about it. Survey does not decide, and writes")
        print("     nothing: neither the protocol nor a proposal file.")
        print("  3. Run: snodo ready to assess readiness")

    return EXIT_PASS


def _print_drift(drift) -> None:
    """Print the protocol-versus-code report.

    Agreement first and at equal length: a page of findings alone reads as an
    indictment of a healthy project, when the ordinary result of this
    comparison is that the two still line up.
    """
    print("Protocol Versus Code:")
    print(f"  {drift.summary}")
    print(f"  Shape: {drift.shape}")
    print()

    print(f"  Agreements ({len(drift.agreements)}):")
    if drift.agreements:
        for item in drift.agreements:
            subject = f" [{item.subject}]" if item.subject else ""
            print(f"  • [{item.check}]{subject} {item.statement}")
            for evidence in item.evidence:
                print(f"      {evidence}")
    else:
        print("    None: no comparison both applied to this protocol's shape and agreed.")
    print()

    print(f"  Divergences ({len(drift.divergences)}):")
    if drift.divergences:
        for finding in drift.divergences:
            print(f"  • [{finding.check}] {finding.subject}")
            print(f"      protocol claims: {finding.claim}")
            print(f"      code shows:      {finding.observation}")
            for evidence in finding.evidence:
                print(f"      {evidence}")
    else:
        print("    None among the comparisons that applied.")
    print()

    print("  Comparisons not made:")
    if drift.not_compared:
        for gap in drift.not_compared:
            print(f"  • [{gap.check}] {gap.reason}")
    else:
        print("    None: every comparison this shape supports was made.")
    print()
