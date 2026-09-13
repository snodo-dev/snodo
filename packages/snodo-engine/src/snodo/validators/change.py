"""The change a post-execute judge is handed, so it never has to find it.

FILE: snodo/validators/change.py (Fixes #267, #269)

A post-execute validator judges the work the coder produced, but until now it
was given no way to learn what that work was: the diff preload existed only
when the project's protocol granted ``read_diff_between_refs``, and not at all
for tool-less single-completion judges.  The judge then burned its turn budget
reconstructing the change — reading every source file, then every test, then
``dist/`` — and frequently reached no verdict.  An observed acceptance judge
ran 41, 49, and 44 turns to no verdict, then passed at turn 26 on a fourth
attempt where it happened to open the three changed files early: same work,
different luck.

The engine already knows what changed: the execute node anchors ``base_ref``
before the coder runs, the coder's writes land as commits on the task branch,
and ``run_validators`` reads ``base_ref..HEAD`` once per validate pass.  This
module carries that knowledge to every post-execute judge as a prompt section.

The distinction is the phase, nothing else.  ``post_execute`` judges see the
change; ``pre_execute`` judges review a proposal and must never be handed a
diff of work that does not exist yet.  Keying this on a validator's id, name,
type, or tool grants reproduces the original defect in a new place.

The section is bounded (Fixes #269): ``render_change_block`` returns at most
``CHANGE_SECTION_CHAR_LIMIT`` characters for a change of any size.  A wave
routinely regenerates a lockfile or a bundle beside the source files that
matter, and that one file runs to tens of thousands of diff lines; the section
now reaches every post-execute judge, including single-completion ones with no
turn budget to recover with.  The unbounded failure is not a clean abstention
— it is a provider context-length error, or a silent mid-hunk cut that leaves
the judge reading half a change it was told is whole, both worse to recognise
from a log than the wandering #267 removed.  So the bound is honest instead:
a diff that fits is shown verbatim; one that does not says it is showing PART
OF the change, names every changed file — names are small and are what the
judge most needs — and spends the rest on content, per file capped, with
likely-regenerated bulk (lockfiles, bundles, minified output) giving way
before hand-edited source.  A judge told the diff is partial can go read the
files it is missing; a judge not told cannot.

The tool's return value stays unbounded: ``read_diff_between_refs`` is a
capability a judge asked for explicitly, often against a ref we did not pick,
and answering an unanticipated question is its prerogative.  Only what the
engine injects unbidden needs a ceiling.  And the ceiling is a constant of
this module, not a config key: a bound a project must discover and set is a
bound that does not get set.
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

#: The one evaluation phase that has a produced change to show.
POST_EXECUTE_PHASE = "post_execute"

#: Hard ceiling on the rendered change section, in characters — roughly 6k
#: tokens at a pessimistic 4 chars/token.  A wave's genuine source changes
#: fit well inside it; a regenerated lockfile does not, and must not be
#: allowed to push them out.  The rest of a judge's prompt (task spec,
#: criteria, other sections) has to fit alongside this.
CHANGE_SECTION_CHAR_LIMIT = 24_000

#: Ceiling on one file's diff inside the section.  A multi-hundred-line
#: change to a single source file fits; a lockfile rewrite does not, so no
#: one file can consume the whole budget.
CHANGE_FILE_CHAR_CAP = 8_000

#: Ceiling on the ref label in the section's framing.  The bound must be
#: input-agnostic — render_change_block cannot assume the shape of a label
#: it is handed — and an unclipped label would crowd out the diff content
#: the budget exists to hold.
_MAX_LABEL_CHARS = 80

#: Paths that are usually machine-regenerated bulk rather than the work
#: under review.  Their names are always listed and their content is shown
#: if it fits; when the budget runs short it is their content that gives
#: way first, because the judge is likelier to need the source hunks.
_BULK_PATH_HINTS = (
    ".lock", ".lockb", "-lock.", "lock.json", "shrinkwrap", ".sum",
    ".min.js", ".min.css", ".map", "bundle.js",
)
_BULK_PATH_PREFIXES = ("dist/", "build/", "node_modules/", ".next/", "out/", "_site/")

#: A truncated section carries this notice outside the code fence.  Its size
#: is reserved out of the budget up front, so the ceiling holds whether or
#: not truncation happens.
_TRUNCATION_NOTICE = (
    "NOTE: this diff is larger than the change section's size budget. What "
    "follows is PART OF the change, not the whole of it: every changed file "
    "is listed by name, and markers show where content was cut or dropped. "
    "Read the named files directly when a criterion needs what is not shown.\n\n"
)

#: Room held back inside the fitted body for its closing markers; their text
#: is short and its length does not depend on the diff.
_MARKER_RESERVE = 220

#: The fallback warning, kept here so its size participates in the budget.
_FALLBACK_NOTE = (
    "NOTE: this diff was read against HEAD~1..HEAD because no "
    "execute-node HEAD anchor was available — it may show the "
    "previous commit rather than this task's produced change.\n"
)


@dataclass(frozen=True)
class ChangeContext:
    """The produced change, read once per validate pass, shared by all judges.

    ``readable`` distinguishes "the range was read and here it is" from "the
    git read failed" so a judge is never told an unreadable range shows no
    changes, and an empty ``diff`` with ``readable=True`` is the honest
    statement that the range records no commits.
    """

    label: str
    diff: str
    is_fallback: bool = False
    readable: bool = True


def build_change_context(
    git_mcp: Any, base_ref: Optional[str]
) -> Optional[ChangeContext]:
    """Read the produced change: ``base_ref..HEAD`` when anchored.

    Falls back to ``HEAD~1..HEAD`` when no execute-node anchor exists, marked
    as a fallback so the judge is told it may be looking at the previous
    unrelated commit (Fixes #109).  Returns None when there is no git view at
    all — the caller then falls back to the engine-recorded artifact list.
    """
    if git_mcp is None:
        return None
    if base_ref:
        label, ref1, ref2, is_fallback = f"{base_ref}..HEAD", base_ref, "HEAD", False
    else:
        label, ref1, ref2, is_fallback = "HEAD~1..HEAD", "HEAD~1", "HEAD", True
    try:
        diff = git_mcp.diff_between_refs(ref1, ref2)
    except Exception:  # noqa: BLE001 — an unreadable range is reported, not fatal
        return ChangeContext(
            label=label,
            diff=f"(unable to read diff {label})",
            is_fallback=is_fallback,
            readable=False,
        )
    return ChangeContext(label=label, diff=str(diff), is_fallback=is_fallback)


def ensure_change_context(context: Any) -> Optional[ChangeContext]:
    """Return this pass's change for a post-execute judge, computing it lazily.

    ``run_validators`` fills ``context.change_context`` once per pass, before
    the validator pool starts; this covers direct calls that bypass the
    runner.  None for every phase but ``post_execute`` — a pre-execute judge
    reviews a proposal, and the range it would be shown is not its work.
    """
    if getattr(context, "phase", "") != POST_EXECUTE_PHASE:
        return None
    change = getattr(context, "change_context", None)
    if change is None:
        change = build_change_context(
            getattr(context, "git_mcp", None), getattr(context, "base_ref", None)
        )
        context.change_context = change
    return change


def render_change_block(
    change: Optional[ChangeContext], artifacts: Optional[List[str]] = None
) -> str:
    """The prompt section showing the change, or "" when there is none.

    Prefers the committed diff; falls back to the engine-recorded artifact
    list when no git range exists, so a post-execute judge still begins
    knowing which files changed.  With neither, nothing is claimed.

    Never returns more than ``CHANGE_SECTION_CHAR_LIMIT`` characters, for a
    change of any size (Fixes #269).  A diff that fits is shown verbatim;
    one that does not is fitted by ``_fit_diff_body`` and carries
    ``_TRUNCATION_NOTICE``, so a judge can tell "this is the change" from
    "this is part of it" and knows which files to go read.  The final clamp
    is a backstop, not the mechanism: each branch budgets its variable parts
    (the ref label is clipped and counted, the notice is reserved).
    """
    label = _clip_label(change.label) if change is not None else ""
    if change is not None:
        header = f"\n## Code Change ({label})\n"
        fallback_note = _FALLBACK_NOTE if change.is_fallback else ""
        tail = (fallback_note + _NOT_REDISCOVER) if change.readable else fallback_note
        parts = [header]
        if change.readable and change.diff.strip():
            budget = max(
                CHANGE_SECTION_CHAR_LIMIT
                - len(header) - len(tail) - len(_TRUNCATION_NOTICE) - 9,
                0,
            )
            body, truncated = _fit_diff_body(change.diff, budget)
            if truncated:
                parts.append(_TRUNCATION_NOTICE)
            parts.append(f"```\n{body}\n```\n")
        elif change.readable:
            parts.append(
                f"(the range {label} records no changes — the produced "
                "commit is empty)\n"
            )
        else:
            parts.append(f"```\n{change.diff}\n```\n")
        parts.append(tail)
        section = "".join(parts)
    elif artifacts:
        section = _render_artifact_list(artifacts)
    else:
        return ""
    if len(section) > CHANGE_SECTION_CHAR_LIMIT:
        section = section[: CHANGE_SECTION_CHAR_LIMIT - 1] + "\n"
    return section


def _clip_label(label: str) -> str:
    """Bound a ref label so framing can never crowd out the content."""
    if len(label) <= _MAX_LABEL_CHARS:
        return label
    return label[: _MAX_LABEL_CHARS - 3] + "..."


def _fit_diff_body(diff: str, budget: int) -> Tuple[str, bool]:
    """Fit a diff into ``budget`` characters, naming every changed file.

    Returns ``(body, truncated)``.  A diff that fits is returned verbatim —
    the untruncated path must not disturb a judge's reading of it, so there
    is no file list or markers when nothing was dropped.

    When it does not fit: the complete file-name list comes first because
    it is what survives truncation and what the judge most needs; content
    follows in priority order — hand-edited files before likely-regenerated
    ones, git's own order preserved within each class — with each file
    capped at ``CHANGE_FILE_CHAR_CAP`` and an explicit marker wherever a
    file's content was cut or dropped entirely.  A file whose capped view
    no longer fits the remaining budget is dropped whole rather than cut
    again mid-hunk: its name is already listed, and the judge is told it is
    missing.
    """
    if len(diff) <= budget:
        return diff, False
    chunks = _file_chunks(diff)
    labels = [label for label, _ in chunks]

    name_room = max(budget - _MARKER_RESERVE, 0)
    lines = [f"CHANGED FILES ({len(labels)}):"]
    used = len(lines[0]) + 1
    listed = 0
    for label in labels:
        if used + len(label) + 5 > name_room:
            break
        lines.append(f"  - {label}")
        used += len(label) + 5
        listed += 1
    if listed < len(labels):
        lines.append(
            f"  ... and {len(labels) - listed} more changed files "
            "(name list truncated)"
        )
    body = "\n".join(lines) + "\n"

    remaining = max(budget - len(body) - _MARKER_RESERVE, 0)
    omitted = 0
    shown_parts: List[str] = []
    for _, chunk in sorted(chunks, key=lambda lc: _is_bulk_regeneration(lc[0])):
        text = chunk
        if len(text) > CHANGE_FILE_CHAR_CAP:
            dropped = len(text) - CHANGE_FILE_CHAR_CAP
            text = (
                text[:CHANGE_FILE_CHAR_CAP]
                + f"\n[... {dropped} more characters of this file's diff "
                "are not shown ...]\n"
            )
        if len(text) + 1 > remaining:
            omitted += 1
            continue
        shown_parts.append(text)
        remaining -= len(text) + 1
    if shown_parts:
        body += "\n".join(shown_parts) + "\n"
    if omitted:
        body += (
            f"[... diff content of {omitted} changed file(s) omitted "
            "entirely; every changed file is named in CHANGED FILES above ...]\n"
        )
    if len(body) > budget:
        body = body[: max(budget - 1, 0)] + "\n"
    return body, True


def _file_chunks(diff: str) -> List[Tuple[str, str]]:
    """Split a unified diff into ``(path, chunk)`` pairs, one per file.

    ``diff --git`` is the only line that reliably delimits files.  The path
    is read from the chunk's ``+++ b/`` line, falling back to ``rename to``
    (pure renames) and ``--- a/`` (deletions, which target ``/dev/null``),
    and finally to the ``diff --git`` header itself, best-effort against a
    header-only chunk such as a binary file's.
    """
    groups: List[List[str]] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            groups.append([line])
        elif groups:
            groups[-1].append(line)
        else:
            groups.append([line])
    return [(_chunk_path(group), "\n".join(group)) for group in groups]


def _chunk_path(lines: List[str]) -> str:
    for prefix, cut in (("+++ b/", 6), ("rename to ", 10), ("--- a/", 6)):
        for line in lines:
            if line.startswith(prefix):
                return line[cut:].strip()
    for line in lines:
        if line.startswith("diff --git "):
            rest = line[len("diff --git "):]
            pos = rest.find(" b/")
            return (rest[pos + 3:] if pos != -1 else rest).strip()
    return "(unattributed diff)"


def _is_bulk_regeneration(path: str) -> bool:
    lowered = path.lower()
    return any(hint in lowered for hint in _BULK_PATH_HINTS) or lowered.startswith(
        _BULK_PATH_PREFIXES
    )


def _render_artifact_list(artifacts: List[str]) -> str:
    """The engine-recorded file list, bounded like the diff it replaces."""
    intro = (
        "\n## Files Changed (engine-recorded)\n"
        "The produced change touches exactly these files (no git range "
        "was available to show the content):\n"
    )
    room = max(CHANGE_SECTION_CHAR_LIMIT - len(intro) - len(_NOT_REDISCOVER) - 64, 0)
    listed = ""
    shown = 0
    for artifact in artifacts:
        line = f"  - {artifact}\n"
        if len(listed) + len(line) > room:
            break
        listed += line
        shown += 1
    if shown < len(artifacts):
        listed += f"  ... and {len(artifacts) - shown} more files (list truncated)\n"
    return intro + listed + _NOT_REDISCOVER


def change_block_for(context: Any) -> str:
    """The change section for this judge's context — phase decides, always."""
    if getattr(context, "phase", "") != POST_EXECUTE_PHASE:
        return ""
    return render_change_block(
        ensure_change_context(context),
        list(getattr(context, "artifacts", None) or []),
    )


_NOT_REDISCOVER = (
    "The engine already established this change for you: which files were "
    "modified and how is stated above. You do not need to search the "
    "repository to find the change — read files only for context a criterion "
    "requires, and deliver your verdict as soon as the criteria are answered.\n"
)
