"""Intake command — propose validator criteria from the repository's decisions.

FILE: snodo/cli/commands/intake_cmd.py

Survey reports what the code shows; a governed protocol's normative content is
prose a human wrote, and survey deliberately does not derive it. But that prose
usually lives in the repository, in the decision records, stating the very
rules the protocol encodes by hand. Intake reads those records and offers the
rules as validator criteria — one proposal at a time, each naming the record it
came from — and the operator accepts or rejects each.

This is a sibling of survey rather than part of it, and the split is the point.
Survey's contract is that it writes nothing; making that contract conditional
on a flag would weaken it for every caller, including the ones that never pass
the flag. Intake's contract is the opposite: it may write to the protocol, but
only what the operator accepts, one proposal at a time, and never a criterion
without the record it came from. Two contracts, two commands.

The boundary between derivation and decision is held in three places:

* ``snodo.survey.criteria`` decides *what is proposable* — a rule's sentence
  lives in the record's Decision section; Context, Consequences, Alternatives
  and Status are left on the table, and the module says why.
* the operator decides *what is accepted*, one prompt per proposal;
* this module decides *how acceptance reaches disk*, and does nothing until it
  has a non-empty accepted set. A rejected run writes no protocol, a proposal
  with no citation is never built, and the protocol is re-verified after the
  append but before the write, so a malformed result is not written either.

``--json`` reports the proposals and writes nothing at all: a machine can see
what would be proposed without a human's acceptance, and the write path stays
human-gated. ``--accept-all`` and ``--reject-all`` are the explicit
non-interactive forms of that gate for scripted use; with neither and no
terminal, intake refuses rather than guessing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List, Optional, Sequence

import typer


def register(app: typer.Typer) -> None:
    """Register the intake command onto *app* (called by discovery)."""

    @app.command()
    def intake(
        validator: Optional[str] = typer.Option(
            None,
            "--validator",
            help=(
                "Validator to append accepted criteria to. "
                "Default: the protocol's architecture validator, else its first."
            ),
        ),
        json: bool = typer.Option(
            False, "--json", help="Emit the proposals as JSON and write nothing",
        ),
        accept_all: bool = typer.Option(
            False, "--accept-all", help="Accept every proposal (explicit)",
        ),
        reject_all: bool = typer.Option(
            False, "--reject-all", help="Reject every proposal (writes nothing)",
        ),
        no_input: bool = typer.Option(
            False, "--no-input", help="Never prompt; requires --accept-all or --reject-all",
        ),
    ):
        """Propose validator criteria from decision records; accept one at a time."""
        return intake_command(
            SimpleNamespace(
                validator=validator,
                json=json,
                accept_all=accept_all,
                reject_all=reject_all,
                no_input=no_input,
            )
        )


def _git_root(json_out: bool) -> Optional[Path]:
    """The repository root, or None after reporting why it could not be found."""
    from snodo.cli.json_output import EXIT_INTERNAL_ERROR, emit_error

    root = Path.cwd()
    try:
        from git import Repo
    except Exception:
        message = "git support is not available, so the repository root cannot be found."
        if json_out:
            emit_error("intake", message, EXIT_INTERNAL_ERROR)
            return None
        print(f"Error: {message}", file=sys.stderr)
        return None
    try:
        repo = Repo(str(root), search_parent_directories=True)
        return Path(repo.working_dir)
    except Exception:
        message = "Not inside a git repository."
        if json_out:
            emit_error("intake", message, EXIT_INTERNAL_ERROR)
            return None
        print(f"Error: {message}", file=sys.stderr)
        return None


def _review(
    proposals: Sequence[Any],
    ask: Callable[[str], str],
) -> List[Any]:
    """Ask the operator about each proposal, in order, and keep the accepted.

    A proposal is shown with its citation before the question, so a rejection
    is a judgement about a rule whose source the operator can see.
    """
    accepted: List[Any] = []
    total = len(proposals)
    for index, proposal in enumerate(proposals, start=1):
        print()
        print(f"[{index}/{total}] {proposal.criterion}")
        attribution = f"    from: {proposal.record_path}"
        if proposal.record_title:
            attribution += f" — {proposal.record_title}"
        print(attribution)
        answer = ask("Accept? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            accepted.append(proposal)
    return accepted


def _write_protocol(
    protocol_path: Path,
    accepted: Sequence[Any],
    requested_validator: Optional[str],
) -> int:
    """Append the accepted criteria and write, after verifying the result."""
    from snodo.cli.json_output import EXIT_INTERNAL_ERROR
    from snodo.compiler.models import Protocol
    from snodo.compiler.verifier import verify_protocol
    from snodo.survey.criteria import (
        UnknownValidatorError,
        append_criteria,
        select_validator,
    )

    import yaml

    try:
        raw = yaml.safe_load(protocol_path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("protocol root is not a mapping")
        validator_id = select_validator(raw, requested_validator)
        updated = append_criteria(
            raw, validator_id, [proposal.criterion for proposal in accepted]
        )
        protocol = Protocol(**updated)
        result = verify_protocol(protocol)
        if not result.passed:
            raise ValueError(
                "the updated protocol would be malformed: "
                + "; ".join(result.errors)
            )
    except UnknownValidatorError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    except Exception as e:
        print(f"Error: refusing to write the protocol: {e}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    protocol_path.write_text(yaml.safe_dump(updated, sort_keys=False))
    print()
    print(
        f"Wrote {len(accepted)} criterion(s) to validator "
        f"'{validator_id}' in {protocol_path}"
    )
    for proposal in accepted:
        print(f"  • {proposal.criterion}")
        print(f"      from: {proposal.record_path}")
    return 0


def intake_command(args, ask: Optional[Callable[[str], str]] = None) -> int:
    """Run intake: propose, review, and write only the accepted criteria."""
    from snodo.cli.json_output import (
        EXIT_INTERNAL_ERROR,
        EXIT_PASS,
        emit_error,
        emit_json,
        schema_name,
    )
    from snodo.protocols import load_protocol
    from snodo.survey.criteria import propose_criteria

    json_out = getattr(args, "json", False)
    accept_all = getattr(args, "accept_all", False)
    reject_all = getattr(args, "reject_all", False)
    no_input = getattr(args, "no_input", False)
    requested_validator = getattr(args, "validator", None)

    project_root = _git_root(json_out)
    if project_root is None:
        return EXIT_INTERNAL_ERROR

    protocol_path = project_root / ".snodo" / "protocol.yml"
    if not protocol_path.exists():
        message = (
            "No .snodo/protocol.yml found. Intake adds criteria to a protocol; "
            "run 'snodo init' first."
        )
        if json_out:
            return emit_error("intake", message, EXIT_INTERNAL_ERROR)
        print(f"Error: {message}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    protocol = load_protocol(protocol_path)
    if protocol is None:
        message = f"The protocol at {protocol_path} could not be loaded."
        if json_out:
            return emit_error("intake", message, EXIT_INTERNAL_ERROR)
        print(f"Error: {message}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    proposals = propose_criteria(project_root)

    if json_out:
        return emit_json(
            {
                "schema": schema_name("intake"),
                "ok": True,
                "project_root": str(project_root),
                "protocol_id": protocol.protocol_id,
                "proposals": [proposal.to_dict() for proposal in proposals],
                "written": False,
            },
            EXIT_PASS,
        )

    print(f"Intake: decision records under {project_root}")
    if not proposals:
        print(
            "  No proposable criteria: no record states a rule in a Decision "
            "section. Nothing written."
        )
        return EXIT_PASS
    print(
        f"  {len(proposals)} criterion proposal(s), each citing the record it "
        "came from."
    )
    for proposal in proposals:
        print(f"  • {proposal.criterion}")
        print(f"      from: {proposal.record_path}")

    if accept_all and reject_all:
        print(
            "Error: --accept-all and --reject-all are mutually exclusive.",
            file=sys.stderr,
        )
        return EXIT_INTERNAL_ERROR

    try:
        if reject_all:
            accepted: List[Any] = []
        elif accept_all:
            accepted = list(proposals)
        elif ask is not None:
            accepted = _review(proposals, ask)
        elif not no_input and sys.stdin.isatty():
            accepted = _review(proposals, input)
        else:
            print(
                "Error: refusing to guess — standard input is not a terminal. "
                "Re-run with --accept-all or --reject-all to decide explicitly.",
                file=sys.stderr,
            )
            return EXIT_INTERNAL_ERROR
    except (EOFError, KeyboardInterrupt):
        print("\nAborted: nothing was written.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if not accepted:
        print("Nothing accepted; nothing written.")
        return EXIT_PASS

    return _write_protocol(protocol_path, accepted, requested_validator)
