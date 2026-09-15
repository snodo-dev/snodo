#!/usr/bin/env python
"""Enforce the engine's closed vocabularies against silent widening.

Why: ADR 045 records that the engine's vocabulary is closed — a validator
returns one of three severities, a halt resolves to one of a fixed set of
outcomes, and a task carries one of a fixed set of statuses. Nothing enforced
it. The record exists because a value was added once (the abstention
``severity=None``) and spread to twenty-one call sites across six packages
before anyone questioned it; removing it cost sixteen hundred lines. A document
prevents the next one only if somebody reads it at the moment they are adding a
value, which is precisely the moment nobody does — the addition always looks
locally reasonable.

The other invariants are not documents. File length, docs coverage and patch
coverage are scripts that run in the gate and fail with a message naming what
broke and what to do. This is one too: adding a value to any of the three
vocabularies fails the build, names the value and the vocabulary it joined, and
says that widening the vocabulary is a decision, not an edit.

The vocabularies are DERIVED from the code, never transcribed here. A list in a
script drifts from the source within weeks and then enforces a fiction. Where
each vocabulary lives today is declared in ``SOURCES``; how it is read is a
small AST walk per source. If a source moves or changes shape the check fails
loudly and says which file to update, rather than silently enforcing less.

Three vocabularies, and deliberately no more: severities, halt types and task
statuses are the ones that have caused this. Speculative vocabularies nobody
has abused are out of scope.

The baseline, not the cliff: today's values are recorded in
scripts/vocabularies_baseline.txt. The check fails when a value appears that is
not baselined; a baselined value that disappears is reported for removal but
does not fail (removal is not the defect). Adding a value cannot be done by
running --update-baseline — that tool refuses while the check is red. The path
is deliberate: write the decision record, then add the line to the baseline in
the same change. There is no inline suppression comment and no per-file
exemption; an escape hatch turns a check into a formality.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

BASELINE_RELATIVE_PATH = Path("scripts") / "vocabularies_baseline.txt"

#: The three closed vocabularies, in a stable order.
VOCABULARIES = ("severity", "halt", "task_status")

#: Human-facing name for each vocabulary, used in failure messages.
VOCABULARY_NAMES = {
    "severity": "severity",
    "halt": "halt type",
    "task_status": "task status",
}

#: Where each vocabulary is defined, as the symbol the reader looks for.
#: ``severity``: the ``Literal[...]`` on ``ValidatorResult.severity``.
#: ``halt``: the keys and canonical values of ``_CANONICAL_HALT``.
#: ``task_status``: the statuses the CLI reports, declared per source below.
SEVERITY_FILE = (
    Path("packages") / "snodo-core" / "src" / "snodo" / "core" / "interfaces.py"
)
SEVERITY_CLASS = "ValidatorResult"
SEVERITY_FIELD = "severity"

HALT_FILE = (
    Path("packages")
    / "snodo-engine"
    / "src"
    / "snodo"
    / "engine"
    / "nodes"
    / "writeback.py"
)
HALT_MAP = "_CANONICAL_HALT"

# Task statuses are reported in more than one place. Each source declares the
# anchors that must be present, so a renamed anchor fails rather than silently
# narrowing what is checked.
TASK_STATUS_SOURCES = {
    Path("snodo") / "cli" / "commands" / "task_cmd.py": {"_TASK_TERMINAL_STATUSES"},
    Path("packages")
    / "snodo-mcp"
    / "src"
    / "snodo"
    / "mcp"
    / "planner.py": {"valid_statuses"},
}

#: Variable names whose string-literal assignments are task statuses, and the
#: dict key under which a reported status travels.
STATUS_VARIABLE = "status"
STATUS_DICT_KEY = "status"


class VocabularySourceError(Exception):
    """A vocabulary's source could not be read — fail loud, never enforce less."""


# ---------------------------------------------------------------------------
# Discovery (from the code)
# ---------------------------------------------------------------------------

def _literal_values(annotation: ast.expr, *, where: str) -> set[str]:
    """Values inside a ``Literal[...]`` annotation, or a bare string Literal."""
    if not isinstance(annotation, ast.Subscript):
        raise VocabularySourceError(f"{where}: expected a Literal[...] annotation")
    if not isinstance(annotation.value, ast.Name) or annotation.value.id != "Literal":
        raise VocabularySourceError(f"{where}: expected a Literal[...] annotation")
    elements = annotation.slice
    if isinstance(elements, ast.Tuple):
        values = {
            elt.value
            for elt in elements.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
    elif isinstance(elements, ast.Constant) and isinstance(elements.value, str):
        values = {elements.value}
    else:
        raise VocabularySourceError(f"{where}: Literal[...] carries no string values")
    if not values:
        raise VocabularySourceError(f"{where}: Literal[...] carries no string values")
    return values


def severity_from_source(source: str) -> set[str]:
    """The values of ``ValidatorResult.severity``'s ``Literal`` annotation."""
    where = f"{SEVERITY_FILE}:{SEVERITY_CLASS}.{SEVERITY_FIELD}"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise VocabularySourceError(f"{where}: cannot parse ({exc})") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == SEVERITY_CLASS:
            for sub in node.body:
                if (
                    isinstance(sub, ast.AnnAssign)
                    and isinstance(sub.target, ast.Name)
                    and sub.target.id == SEVERITY_FIELD
                ):
                    return _literal_values(sub.annotation, where=where)
    raise VocabularySourceError(
        f"{where}: not found — the severity field moved; update "
        "scripts/enforce_vocabularies.py to match."
    )


def _string_dict(node: ast.Dict, *, where: str) -> tuple[set[str], set[str]]:
    """String keys and string values of a dict literal, or fail."""
    keys: set[str] = set()
    values: set[str] = set()
    for key, value in zip(node.keys, node.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise VocabularySourceError(f"{where}: a key is not a string literal")
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            raise VocabularySourceError(
                f"{where}: the value for {key.value!r} is not a string literal"
            )
        keys.add(key.value)
        values.add(value.value)
    if not keys:
        raise VocabularySourceError(f"{where}: the dict is empty")
    return keys, values


def halt_from_source(source: str) -> set[str]:
    """The raw keys and canonical values of ``_CANONICAL_HALT``, unioned.

    Both are closed: a raw halt type the loop sets and the canonical outcome it
    resolves to. Adding either widens the vocabulary.
    """
    where = f"{HALT_FILE}:{HALT_MAP}"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise VocabularySourceError(f"{where}: cannot parse ({exc})") from exc
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == HALT_MAP:
                    if not isinstance(node.value, ast.Dict):
                        raise VocabularySourceError(
                            f"{where}: not a dict literal — the halt map moved "
                            "or changed shape; update "
                            "scripts/enforce_vocabularies.py to match."
                        )
                    keys, values = _string_dict(node.value, where=where)
                    return keys | values
    raise VocabularySourceError(
        f"{where}: not found — the halt map moved; update "
        "scripts/enforce_vocabularies.py to match."
    )


def _string_collection(node: ast.expr, *, where: str) -> set[str]:
    """The string literals in a set/tuple/list literal, or fail."""
    if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        raise VocabularySourceError(f"{where}: expected a set of string literals")
    values = {
        elt.value
        for elt in node.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    }
    if not values:
        raise VocabularySourceError(f"{where}: the set carries no string values")
    return values


def task_status_from_source(
    source: str, *, where: str, anchors: set[str]
) -> set[str]:
    """Task statuses reported by one source.

    A status is any of: a string in an anchor set (``_TASK_TERMINAL_STATUSES`` /
    ``valid_statuses``); a string assigned to a variable named ``status``; or a
    string under a dict key ``"status"``. Every declared anchor must be present,
    so a rename fails instead of quietly checking less.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise VocabularySourceError(f"{where}: cannot parse ({exc})") from exc

    statuses: set[str] = set()
    seen_anchors: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in anchors:
                    seen_anchors.add(target.id)
                    statuses |= _string_collection(
                        node.value, where=f"{where}:{target.id}"
                    )
                elif target.id == STATUS_VARIABLE and isinstance(
                    node.value, ast.Constant
                ) and isinstance(node.value.value, str):
                    statuses.add(node.value.value)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == STATUS_DICT_KEY
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    statuses.add(value.value)

    missing = anchors - seen_anchors
    if missing:
        raise VocabularySourceError(
            f"{where}: anchor(s) {sorted(missing)} not found — the status "
            "vocabulary moved; update scripts/enforce_vocabularies.py to match."
        )
    return statuses


def collect_vocabularies(repo_root: Path) -> dict[str, set[str]]:
    """Read every vocabulary from the code under *repo_root*.

    Raises :class:`VocabularySourceError` if any source is missing or has
    changed shape — the check must fail loudly rather than enforce nothing.
    """
    def read(path: Path) -> str:
        full = repo_root / path
        try:
            return full.read_text(encoding="utf-8")
        except OSError as exc:
            raise VocabularySourceError(f"{full}: cannot read ({exc})") from exc

    discovered: dict[str, set[str]] = {
        "severity": severity_from_source(read(SEVERITY_FILE)),
        "halt": halt_from_source(read(HALT_FILE)),
    }

    task_statuses: set[str] = set()
    for path, anchors in TASK_STATUS_SOURCES.items():
        task_statuses |= task_status_from_source(
            read(path), where=path.as_posix(), anchors=set(anchors)
        )
    discovered["task_status"] = task_statuses
    return discovered


# ---------------------------------------------------------------------------
# Baseline (closed-vocabulary record) and the check
# ---------------------------------------------------------------------------

def parse_baseline(baseline_path: Path) -> dict[str, set[str]]:
    """Read ``<vocabulary> <value>`` entries into a per-vocabulary set."""
    entries: dict[str, set[str]] = {name: set() for name in VOCABULARIES}
    if not baseline_path.is_file():
        return entries
    for lineno, raw in enumerate(
        baseline_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] not in VOCABULARIES:
            raise ValueError(
                f"{baseline_path}:{lineno}: expected '<vocabulary> <value>' with "
                f"vocabulary in {VOCABULARIES}, got: {raw!r}"
            )
        entries[parts[0]].add(parts[1].strip())
    return entries


class Report:
    """Outcome of one vocabulary check.

    A plain class rather than a dataclass: the repo's script tests load this
    file with importlib without registering it in ``sys.modules``, and
    @dataclass breaks under that on Python 3.13+.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.removed: list[tuple[str, str]] = []
        self.discovered: dict[str, set[str]] = {}


def check(repo_root: Path, baseline: dict[str, set[str]]) -> Report:
    """Fail for every value that appeared; note every value that left."""
    report = Report()
    try:
        report.discovered = collect_vocabularies(repo_root)
    except VocabularySourceError as exc:
        report.failures.append(str(exc))
        return report

    for vocabulary in VOCABULARIES:
        current = report.discovered.get(vocabulary, set())
        recorded = baseline.get(vocabulary, set())
        for value in sorted(current - recorded):
            report.failures.append(
                f"{VOCABULARY_NAMES[vocabulary]} vocabulary gained {value!r}. "
                "This vocabulary is closed: a new value is a decision, not an "
                "edit. Write a decision record (see ADR 045) for it, then add "
                f"`{vocabulary} {value}` to scripts/vocabularies_baseline.txt in "
                "the same change. Do not widen the vocabulary to make this go "
                "away."
            )
        for value in sorted(recorded - current):
            report.removed.append((vocabulary, value))
    return report


def render_summary(report: Report) -> list[str]:
    """Lines describing values that left the vocabulary, and the OK line."""
    lines: list[str] = []
    for vocabulary, value in report.removed:
        lines.append(
            f"{VOCABULARY_NAMES[vocabulary]} {value!r} is baselined but no "
            "longer set anywhere in the source — run --update-baseline to drop "
            "it; it cannot come back."
        )
    if not report.failures and not report.removed:
        counts = ", ".join(
            f"{len(report.discovered.get(name, set()))} {VOCABULARY_NAMES[name]}"
            for name in VOCABULARIES
        )
        lines.append(f"Vocabulary check OK: {counts} values, all baselined.")
    return lines


def write_baseline(baseline_path: Path, report: Report, *, is_seed: bool) -> int:
    """Record the current values; refuse while a value has appeared unrecorded.

    This is what makes adding a value deliberate: the tool that rewrites the
    record refuses to run while the check is red, so the only way to add a value
    is to write the decision record and hand-edit the baseline in one change.
    The one exception is the initial recording: with an empty baseline every
    value is "new" and the check cannot be green, so the current values are
    seeded once. After that the tool only ever refuses on a new value.
    """
    if not is_seed and report.failures:
        print("Refusing to update the baseline while the check fails:")
        for failure in report.failures:
            print(f"- {failure}")
        return 1
    header = "\n".join(
        [
            "# Closed-vocabulary record — one entry per value the engine's "
            "closed vocabularies carry.",
            "#",
            "# Each line is '<vocabulary> <value>': severity, halt (raw halt "
            "types and canonical outcomes),",
            "# or task_status. The check that reads this derives the values "
            "from the code;",
            "# an entry here is the record that a value was decided, not the "
            "definition of it.",
            "#",
            "# A value that appears in the source and not here fails the "
            "build. Run",
            "#   uv run python scripts/enforce_vocabularies.py --update-baseline",
            "# only to record a value you have written a decision record for, "
            "or to drop",
            "# one that was removed — it refuses while the check is red. There "
            "is no",
            "# suppression comment on purpose: widening a closed vocabulary is "
            "a decision.",
        ]
    )
    body = "".join(
        f"{vocabulary} {value}\n"
        for vocabulary in VOCABULARIES
        for value in sorted(report.discovered.get(vocabulary, set()))
    )
    baseline_path.write_text(header + "\n\n" + body, encoding="utf-8")
    print(f"Wrote closed-vocabulary baseline to {baseline_path}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enforce the engine's closed vocabularies."
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help=(
            "Baseline file (default: "
            "<repo>/scripts/vocabularies_baseline.txt)."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Record the current values (refuses while a new value is unrecorded).",
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
        print(f"Vocabulary check failed: {exc}")
        return 1

    report = check(repo_root, baseline)

    for failure in report.failures:
        print(f"FAIL: {failure}")
    for line in render_summary(report):
        print(line)

    if args.update_baseline:
        # Seed on an empty baseline (the first recording); afterwards the tool
        # only ever refuses on a value that appeared without a record.
        recorded_any = any(baseline.get(name) for name in VOCABULARIES)
        return write_baseline(baseline_path, report, is_seed=not recorded_any)

    if report.failures:
        print(f"Vocabulary check failed: {len(report.failures)} violation(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
