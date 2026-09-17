"""The shape of a coder's report about the run it just did.

FILE: snodo/coders/report.py

The engine hands a coder a prompt, waits, and then reads the worktree.
Everything else about the run is inference: it cannot tell a coder that
finished and found nothing to change from one that ran out of turns, ran out
of context, or simply stopped, so every diagnosis of a failed run is
archaeology on a prose tail — and the engine must not scrape a verdict out of
prose (ADR 045 rejects exactly that move for a judge's output).

This module declares only the SHAPE. No adapter fills it in yet and nothing
reads it; later tickets do those. The report gives a coder one honest channel
to say, in structured terms, what it did, how far it got, and why it stopped,
so a coder can hand the engine a structured account it never has to parse out
of free text.

A report is best-effort EVIDENCE FROM A NON-DETERMINISTIC PARTICIPANT. A coder
may do the work and forget to report, report only half of it, or report
something that did not happen. Every field is therefore optional: absent is
normal and never an error. A malformed report is discarded with a log line
(see :func:`parse_coder_report`), never a halt — a coder's misstatement must
not be able to stop a run the worktree could otherwise have judged on its own
merits.

The report is EVIDENCE, NEVER A VERDICT, and this is the constraint the type
exists to make impossible to misuse later:

  * The worktree is, and stays, the only authority on what was written. A
    file a coder claims to have created but that is not in the tree was not
    created; a file it omits but that is in the tree was. The report's file
    list never adds to or subtracts from the diff the engine reads back.

  * Nothing a coder reports may cause a pass. A ``completed`` stop reason is a
    coder's own account of stopping, not the protocol's conclusion that the
    task was done; the validators, not the coder, decide that. The shape
    carries no verdict, severity, or status field precisely so that no later
    reader can mistake a coder's self-report for a judgement — and a coder
    that reports nothing (the common case) is indistinguishable, to any
    downstream decision, from one that simply did not speak.

The vocabulary of *why a run stopped* is deliberately closed and small
(:data:`STOP_REASONS`); a vocabulary that grows later is worse than one
thought through now. Each member's justification lives in the decision
record, ADR 048.
"""

from __future__ import annotations

import logging
from typing import Any, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

_logger = logging.getLogger(__name__)

__all__ = [
    "StopReason",
    "STOP_REASONS",
    "CoderFileChange",
    "CoderReport",
    "parse_coder_report",
    "build_coder_report",
]

#: The file operation a coder claims to have performed. ``created``,
#: ``modified`` and ``deleted`` describe the coder's intent; the worktree's
#: change type is what the engine actually reads back and trusts.
FileChangeKind = Literal["created", "modified", "deleted"]

#: Why a coder stopped. A closed set: see ADR 048 for why each member earns
#: its place and why a coder killed by the operator's wall-clock timeout is
#: absent from it (it never returns to report). ``completed`` covers both "I
#: changed files" and "I found nothing to change" — the file list, not a
#: separate reason, is what tells those apart.
StopReason = Literal[
    "completed",
    "turn_budget",
    "context_budget",
    "provider_fault",
    "abandoned",
]

#: :data:`StopReason` as a runtime-checkable set. The closure test pins the
#: ``Literal`` and this frozenset to each other so the type and the accepted
#: set cannot drift apart.
STOP_REASONS: frozenset[str] = frozenset(
    {"completed", "turn_budget", "context_budget", "provider_fault", "abandoned"}
)


class CoderFileChange(BaseModel):
    """One file a coder believes it touched, and how.

    Best-effort evidence: the claim is the coder's, and the worktree is the
    authority. The path is workspace-relative as the coder saw it; the engine
    does not resolve or trust it here.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: FileChangeKind


class CoderReport(BaseModel):
    """What a coder says it did, how far it got, and why it stopped.

    Every field is optional. A coder reports whichever of these it actually
    knows, and an entirely empty report (:meth:`empty`) is valid — a coder may
    do the work and forget to say so. Absence is never an error.

    See the module docstring for the governing constraint: this is EVIDENCE
    FROM A NON-DETERMINISTIC PARTICIPANT, never a verdict, and nothing in it
    may cause a pass or override the worktree. There is deliberately no
    ``passed``/``severity``/``status`` field to add later — a coder's account
    of stopping is not a judgement of the work, and the absence of such a
    field is pinned by test so the next reader cannot introduce one by
    accident.
    """

    model_config = ConfigDict(extra="forbid")

    # --- What it did -------------------------------------------------------
    #: Files the coder believes it created, modified or deleted. Never the
    #: engine's read of the diff; the worktree is the only authority on that.
    files: Optional[List[CoderFileChange]] = None

    # --- How far it got (whichever this coder knows) -----------------------
    #: Turns the coder took, and the turns it was working against.
    turns_used: Optional[int] = None
    turns_available: Optional[int] = None
    #: Tokens the coder spent, and the context window it was spending them in.
    tokens_used: Optional[int] = None
    context_window: Optional[int] = None
    #: Wall-clock milliseconds the coder's own run took, as it measured them.
    wall_time_ms: Optional[int] = None

    # --- Why it stopped ----------------------------------------------------
    #: One value from the closed :data:`STOP_REASONS` set, or absent when the
    #: coder does not say. Never mapped onto a halt here.
    stop_reason: Optional[StopReason] = None

    @classmethod
    def empty(cls) -> "CoderReport":
        """A report with every field absent — a coder that did the work and
        said nothing. Valid by construction, and the shape every tolerant
        parse falls back to when a report cannot be understood."""
        return cls()


def parse_coder_report(raw: object) -> Optional[CoderReport]:
    """Parse a coder's best-effort report, or return ``None``.

    Tolerant by contract: a coder is a non-deterministic participant, so a
    report that is absent (``None``), empty, or malformed is *not* an error
    and must never halt a run that the worktree can be judged on its own for.
    A well-formed report parses to a :class:`CoderReport`; anything that does
    not parse — wrong shape, an unknown ``stop_reason``, a field of the wrong
    type, an unexpected key — is discarded with a single warning line naming
    why, and ``None`` is returned.

    Returns ``None`` for absent or malformed input; the caller treats a missing
    report exactly like an empty one. It never raises.
    """
    if raw is None:
        return None
    if isinstance(raw, CoderReport):
        return raw
    if not isinstance(raw, dict):
        _logger.warning(
            "Discarding coder report: expected a mapping, got %s; treating as "
            "absent (a coder's report is evidence, never a halt).",
            type(raw).__name__,
        )
        return None
    if not raw:
        # An explicitly empty mapping is a valid report with nothing said.
        return CoderReport.empty()
    try:
        return CoderReport.model_validate(raw)
    except ValidationError as exc:
        _logger.warning(
            "Discarding malformed coder report (%d field error(s): %s); "
            "treating it as absent. A coder's report is best-effort evidence "
            "from a non-deterministic participant and is never a verdict.",
            len(exc.errors()),
            "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: "
                f"{err['msg']}"
                for err in exc.errors()
            ),
        )
        return None


def build_coder_report(
    stop_reason: Optional[StopReason] = None,
    submitted_files: Optional[Mapping[str, Mapping[str, Any]]] = None,
    turns_used: Optional[int] = None,
    turns_available: Optional[int] = None,
    tokens_used: Optional[int] = None,
    elapsed_ms: Optional[int] = None,
    workspace: Any = None,
) -> Optional[CoderReport]:
    """Assemble the report a coder's loop already holds the facts for.

    The loop knows the files it staged, the turns it used, the tokens it saw,
    how long it ran and why it stopped; this shapes those into the ADR 048
    report. Best-effort by construction: it never raises and returns ``None``
    when the facts cannot be shaped, so a coder's own accounting can never
    fail a run the worktree could be judged on its own merits.
    """
    try:
        return CoderReport(
            files=_reported_file_changes(submitted_files, workspace),
            turns_used=turns_used,
            turns_available=turns_available,
            tokens_used=tokens_used,
            wall_time_ms=elapsed_ms,
            stop_reason=stop_reason,
        )
    except Exception as e:
        _logger.warning(
            "Discarding unbuildable coder report (run unaffected; a coder's "
            "report is evidence, never a halt): %s: %s", type(e).__name__, e,
        )
        return None


def _reported_file_changes(
    submitted_files: Optional[Mapping[str, Mapping[str, Any]]],
    workspace: Any,
) -> Optional[List[CoderFileChange]]:
    """Shape the loop's staged file operations into reported file changes."""
    changes: List[CoderFileChange] = []
    for path, operation in (submitted_files or {}).items():
        action = (operation or {}).get("action", "write")
        if action == "delete":
            changes.append(CoderFileChange(path=str(path), kind="deleted"))
        else:
            changes.append(CoderFileChange(
                path=str(path),
                kind="created" if _is_new_path(path, workspace) else "modified",
            ))
    return changes or None


def _is_new_path(path: str, workspace: Any) -> bool:
    """Whether the pre-write tree has no file at *path* (best-effort).

    The loop stages writes in memory, so the workspace still holds the tree as
    the coder found it: a path that exists is being modified, one that does
    not is being created. When the workspace cannot answer, the report says
    ``modified`` rather than over-claiming a creation.
    """
    probe = getattr(workspace, "file_exists", None)
    if not callable(probe):
        return False
    try:
        return not bool(probe(path))
    except Exception:
        return False
