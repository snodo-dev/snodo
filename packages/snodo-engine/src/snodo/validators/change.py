"""The change a post-execute judge is handed, so it never has to find it.

FILE: snodo/validators/change.py (Fixes #267)

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
"""

from dataclasses import dataclass
from typing import Any, List, Optional

#: The one evaluation phase that has a produced change to show.
POST_EXECUTE_PHASE = "post_execute"


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
    """
    if change is not None:
        parts = ["\n", f"## Code Change ({change.label})\n"]
        if change.readable and change.diff.strip():
            parts.append(f"```\n{change.diff}\n```\n")
        elif change.readable:
            parts.append(
                f"(the range {change.label} records no changes — the produced "
                "commit is empty)\n"
            )
        else:
            parts.append(f"```\n{change.diff}\n```\n")
        if change.is_fallback:
            parts.append(
                "NOTE: this diff was read against HEAD~1..HEAD because no "
                "execute-node HEAD anchor was available — it may show the "
                "previous commit rather than this task's produced change.\n"
            )
        if change.readable:
            parts.append(_NOT_REDISCOVER)
        return "".join(parts)
    if artifacts:
        listed = "".join(f"  - {a}\n" for a in artifacts)
        return (
            "\n## Files Changed (engine-recorded)\n"
            "The produced change touches exactly these files (no git range "
            "was available to show the content):\n"
            + listed
            + _NOT_REDISCOVER
        )
    return ""


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
