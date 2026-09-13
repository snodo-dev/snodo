#!/usr/bin/env python
"""Enforce the file-length ratchet: at most 1,000 lines of code per file.

Why a script and not the linter: ruff implements no module-length rule at all
(pylint's ``too-many-lines`` has no ruff equivalent), so no configuration of
the linter already in the gate could catch this. Nothing else in the repo
watched file size, a 500-line target recorded in docs/specs was never
enforced, and ``snodo/cli/commands/run_cmd.py`` drifted from 569 lines to
1,759 without a single signal.

What counts is lines of *code*, not physical lines: blank lines, comment
lines and docstrings do not count. This codebase explains itself in long
prose comments — that is deliberate, and a check that counted them would
apply steady pressure to delete the best thing about the code in order to
satisfy a number. A file of nine hundred lines of logic and four hundred
lines of explanation is in good health.

The ratchet, not the cliff: files already over the limit are listed, with
their current count, in scripts/file_length_baseline.txt. That file is a
debt list that can only be paid down — a baselined file may shrink but may
never grow past its recorded count, a file not in the baseline must stay
under the limit, and a file that drops under the limit leaves the baseline
and cannot return. There is deliberately no per-file suppression comment:
an escape hatch turns a ratchet into a formality.

Tests are out of scope: a long test file reads differently from a long
module and the case for constraining it has not been made.
"""

from __future__ import annotations

import argparse
import os
import sys
import tokenize
from pathlib import Path

LIMIT = 1000
BASELINE_RELATIVE_PATH = Path("scripts") / "file_length_baseline.txt"

# Directories the walk never enters. ``tests`` excluded on purpose (see the
# module docstring); the rest are environments, caches and build residue.
EXCLUDED_DIR_NAMES = {"__pycache__", "node_modules", "tests"}


def count_loc(source: str) -> int:
    """Return the number of lines of code in ``source``.

    A line counts if it carries a code token. Comment and blank lines are
    skipped, as are the physical lines of a standalone string statement —
    the module, class and function docstrings that carry this repo's
    explanatory prose, plus the occasional bare-string block comment.
    """
    try:
        tokens = list(tokenize.generate_tokens(iter(source.splitlines(True)).__next__))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise ValueError(f"file does not tokenize: {exc}") from exc

    code_lines: set[int] = set()
    docstring_lines: set[int] = set()
    logical_line: list[tokenize.TokenInfo] = []

    for tok in tokens:
        if tok.type in (
            tokenize.ENCODING,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.NL,
            tokenize.COMMENT,
        ):
            continue
        if tok.type == tokenize.NEWLINE:
            # A logical line made of exactly one string token is a docstring
            # (or a bare string used as a comment); none of its physical
            # lines are code.
            if len(logical_line) == 1 and logical_line[0].type == tokenize.STRING:
                first = logical_line[0]
                docstring_lines.update(range(first.start[0], first.end[0] + 1))
            logical_line = []
            continue
        logical_line.append(tok)
        code_lines.update(range(tok.start[0], tok.end[0] + 1))

    return len(code_lines - docstring_lines)


def discover_python_files(repo_root: Path) -> list[Path]:
    """Return every first-party .py file under ``repo_root``, tests excluded."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in EXCLUDED_DIR_NAMES
            and not d.endswith("egg-info")
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def parse_baseline(baseline_path: Path) -> dict[str, int]:
    """Read the debt list: ``<loc> <path>`` entries, ``#`` introduces comments."""
    entries: dict[str, int] = {}
    if not baseline_path.is_file():
        return entries
    for lineno, raw in enumerate(
        baseline_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            raise ValueError(
                f"{baseline_path}:{lineno}: expected '<loc> <path>', got: {raw!r}"
            )
        entries[parts[1].strip()] = int(parts[0])
    return entries


class Report:
    """Outcome of one ratchet check.

    A plain class rather than a dataclass: the repo's script tests load this
    file with importlib without registering it in ``sys.modules``, and
    @dataclass breaks under that on Python 3.13+.
    """

    def __init__(self, limit: int = LIMIT) -> None:
        self.limit = limit
        self.failures: list[str] = []
        self.tighten: list[tuple[str, int, int]] = []
        self.leave_baseline: list[tuple[str, int]] = []
        self.missing: list[str] = []
        self.checked = 0
        self.baselined: list[tuple[str, int, int]] = []


def check(repo_root: Path, baseline: dict[str, int], limit: int = LIMIT) -> Report:
    """Measure every file against the limit and the ratchet."""
    report = Report(limit=limit)
    seen: set[str] = set()
    for path in discover_python_files(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        seen.add(relative)
        try:
            loc = count_loc(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            report.failures.append(f"{relative}: cannot measure ({exc}).")
            continue
        report.checked += 1
        baseline_count = baseline.get(relative)

        if baseline_count is None:
            if loc > limit:
                report.failures.append(
                    f"{relative}: {loc} lines of code — over the {limit} limit "
                    f"by {loc - limit}, and not in the baseline. A file that "
                    "crosses the limit must be split now, not baselined; the "
                    "baseline is a frozen debt list, not a place to park new "
                    "work."
                )
            continue

        if loc > baseline_count:
            report.failures.append(
                f"{relative}: {loc} lines of code — its baseline is "
                f"{baseline_count} but it has grown by "
                f"{loc - baseline_count}. The baseline may only fall. "
                f"(Currently {loc - limit} over the {limit} limit; shrink the "
                f"file back below {baseline_count} or split it — do not edit "
                "the baseline upward.)"
            )
            continue

        if loc > limit:
            report.baselined.append((relative, loc, baseline_count))
            if loc < baseline_count:
                report.tighten.append((relative, loc, baseline_count))
        else:
            report.leave_baseline.append((relative, loc))

    # Baselined paths that no longer exist: the file was deleted or split.
    # That is debt paid in full, but the entry must leave the list.
    report.missing = sorted(set(baseline) - seen)
    return report


def render_summary(report: Report) -> list[str]:
    """Lines describing the state of the ratchet, debt included."""
    lines: list[str] = []
    for relative in report.missing:
        lines.append(
            f"{relative}: baseline entry for a file that no longer exists — "
            "debt paid by deletion or split; run --update-baseline to drop "
            "the entry."
        )
    for relative, loc in report.leave_baseline:
        lines.append(
            f"{relative}: {loc} lines of code, now under the "
            f"{report.limit}-line limit — remove it from the baseline; it "
            "cannot come back."
        )
    for relative, loc, baseline_count in report.tighten:
        lines.append(
            f"{relative}: {loc} lines of code, down {baseline_count - loc} from "
            f"its baseline of {baseline_count} — run --update-baseline to "
            "tighten the ratchet."
        )
    if not report.failures:
        debt = sum(loc for _, loc, _ in report.baselined)
        owed = sum(loc - report.limit for _, loc, _ in report.baselined)
        lines.append(
            f"Ratchet OK: {report.checked} files checked against the "
            f"{report.limit}-line limit; {len(report.baselined)} baselined "
            f"files carry {debt} lines of code ({owed} over the limit) and "
            "may only shrink."
        )
    return lines


def write_baseline(
    baseline_path: Path, report: Report, limit: int = LIMIT
) -> int:
    """Pay the debt down: rewrite the baseline at current counts.

    Only ever lowers the ratchet. Refuses if any measured file is above its
    recorded baseline (which ``check`` already fails) or if any file not in
    the baseline is over the limit — the update tool cannot admit new debt,
    park a fresh violation, or forget a listed file that has grown. Files
    that dropped below the limit leave the list for good.
    """
    grown = list(report.failures)
    if grown:
        print("Refusing to update the baseline while the check fails:")
        for failure in grown:
            print(f"- {failure}")
        return 1
    new_entries = {relative: loc for relative, loc, _ in report.baselined}
    header = "\n".join(
        [
            "# File-length debt list — one entry per file over the "
            f"{limit}-lines-of-code limit.",
            "#",
            "# Each line is '<loc> <path>': the file's lines-of-code count "
            "when the",
            "# debt was recorded. The number may fall, never rise. Run",
            "#   uv run python scripts/enforce_file_length.py --update-baseline",
            "# to pay down: it lowers counts to current and drops files that "
            "came",
            "# under the limit — once out, a file cannot come back (it is then "
            "held",
            f"# to the {limit}-line limit itself).",
            "#",
            "# There is no suppression mechanism on purpose. If you think you "
            "need",
            "# an exception here, the file wants splitting.",
        ]
    )
    body = "".join(
        f"{loc:5d} {relative}\n"
        for relative, loc in sorted(new_entries.items(), key=lambda kv: -kv[1])
    )
    baseline_path.write_text(header + "\n\n" + body, encoding="utf-8")
    print(f"Wrote baseline with {len(new_entries)} debt entries to {baseline_path}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce the per-file lines-of-code ratchet."
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline file (default: <repo>/scripts/file_length_baseline.txt).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=LIMIT,
        help=f"Lines-of-code limit per file (default: {LIMIT}).",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline at current counts (only ever tightens).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    baseline_path = (
        Path(args.baseline)
        if args.baseline
        else repo_root / BASELINE_RELATIVE_PATH
    )

    try:
        baseline = parse_baseline(baseline_path)
    except ValueError as exc:
        print(f"File-length check failed: {exc}")
        return 1

    report = check(repo_root, baseline, limit=args.limit)

    for failure in report.failures:
        print(f"FAIL: {failure}")
    for line in render_summary(report):
        print(line)

    if report.failures:
        print(f"File-length check failed: {len(report.failures)} violation(s).")
        return 1

    if args.update_baseline:
        return write_baseline(baseline_path, report, limit=args.limit)

    return 0


if __name__ == "__main__":
    sys.exit(main())
