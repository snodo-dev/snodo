"""Tests for scripts/enforce_vocabularies.py.

These exercise the check's behaviour on synthetic repositories — what fails
when a value appears, what passes when one is removed, that the values are read
from the source's own syntax rather than a list in the script, and that a moved
source fails loudly instead of enforcing nothing. One integration test asserts
the real repository passes against its recorded baseline.
"""

import contextlib
import importlib.util
import io
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enforce_vocabularies.py"
_spec = importlib.util.spec_from_file_location("enforce_vocabularies", SCRIPT_PATH)
vocab_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(vocab_check)


SEVERITY_TEMPLATE = """\
from typing import Literal


class ValidatorResult:
    severity: Literal[{values}]
"""

HALT_TEMPLATE = """\
_CANONICAL_HALT = {{
{entries}
}}
"""

TASK_STATUS_TEMPLATE = """\
_TASK_TERMINAL_STATUSES = {{{terminal}}}


def f():
    status = "{assigned}"
    return status
"""

PLANNER_TEMPLATE = """\
def update_status(status):
    valid_statuses = {{{planner}}}
"""


def _severity(*values: str) -> str:
    return SEVERITY_TEMPLATE.format(values=", ".join(f'"{v}"' for v in values))


def _halt(entries: dict[str, str]) -> str:
    return HALT_TEMPLATE.format(
        entries="\n".join(f'    "{k}": "{v}",' for k, v in entries.items())
    )


def _make_repo(
    tmp_path: Path,
    *,
    severity: str = '"pass", "warn", "blocker"',
    halt: dict[str, str] | None = None,
    terminal: str = '"completed", "failed"',
    assigned: str = "merged",
    planner: str = '"pending", "completed"',
    baseline: str | None = None,
) -> Path:
    """Build a minimal repo with the three vocabulary sources in their paths."""
    files = {
        vocab_check.SEVERITY_FILE: SEVERITY_TEMPLATE.format(values=severity),
        vocab_check.HALT_FILE: _halt(halt if halt is not None else {"blocked": "blocker"}),
    }
    for path in vocab_check.TASK_STATUS_SOURCES:
        if path.name == "task_cmd.py":
            files[path] = TASK_STATUS_TEMPLATE.format(
                terminal=terminal, assigned=assigned
            )
        else:
            files[path] = PLANNER_TEMPLATE.format(planner=planner)
    for rel, content in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    baseline_path = tmp_path / "baseline.txt"
    if baseline is not None:
        baseline_path.write_text(baseline, encoding="utf-8")
    return baseline_path


def run_check(tmp_path: Path, baseline_path: Path, *args: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = vocab_check.main(
            ["--repo", str(tmp_path), "--baseline", str(baseline_path), *args]
        )
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# Derived from source, not transcribed
# ---------------------------------------------------------------------------


class TestReadsFromSource:
    def test_severity_values_are_read_from_the_literal_annotation(self, tmp_path):
        """The severity set is whatever the source's Literal carries."""
        baseline = _make_repo(tmp_path, severity='"alpha", "beta"', baseline="")
        report = vocab_check.check(tmp_path, vocab_check.parse_baseline(baseline))
        assert report.discovered["severity"] == {"alpha", "beta"}

    def test_halt_values_are_the_keys_and_canonical_values_of_the_map(self, tmp_path):
        """Both the raw halt types (keys) and canonical outcomes (values) count."""
        baseline = _make_repo(
            tmp_path,
            halt={"blocked": "blocker", "oops": "internal_error"},
            baseline="",
        )
        report = vocab_check.check(tmp_path, vocab_check.parse_baseline(baseline))
        assert report.discovered["halt"] == {
            "blocked", "blocker", "oops", "internal_error",
        }

    def test_task_statuses_are_read_from_the_anchors_and_assignments(self, tmp_path):
        """Anchor sets, ``status = "..."`` assignments and ``"status"`` dict
        values are all discovered from the source text."""
        baseline = _make_repo(
            tmp_path,
            terminal='"completed", "failed"',
            assigned="merged",
            planner='"pending", "completed"',
            baseline="",
        )
        report = vocab_check.check(tmp_path, vocab_check.parse_baseline(baseline))
        assert report.discovered["task_status"] == {
            "completed", "failed", "merged", "pending",
        }

    def test_script_holds_no_literal_vocabulary_values(self):
        """The script must not carry a transcribed copy of any vocabulary.

        A literal list in the script drifts from the source and then enforces a
        fiction; this proves the values come from the code by checking the
        script's own text does not name them.
        """
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for value in ("pass", "warn", "blocker"):
            # The failure message quotes ``value`` generically; what must not
            # appear is a literal set of vocabulary values.
            assert f'"{value}"' not in source, (
                f"the script hard-codes severity {value!r}; it must read the "
                "vocabularies from source"
            )
        for value in ("environment_error", "no_file_operations", "recovery_stalled"):
            assert f'"{value}"' not in source
        for value in ("in_progress", "unmerged", "errored"):
            assert f'"{value}"' not in source


# ---------------------------------------------------------------------------
# The ratchet: a new value fails, a removed value does not
# ---------------------------------------------------------------------------


class TestRatchet:
    def test_new_severity_fails_and_names_the_value(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            severity='"pass", "warn", "blocker"',
            baseline="severity pass\nseverity warn\nseverity blocker\n",
        )
        # Widen the annotation in the source.
        (tmp_path / vocab_check.SEVERITY_FILE).write_text(
            _severity("pass", "warn", "blocker", "info"), encoding="utf-8"
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "'info'" in out
        assert "severity" in out
        assert "decision record" in out

    def test_new_halt_type_fails_and_names_the_value(self, tmp_path):
        baseline = _make_repo(
            tmp_path, halt={"blocked": "blocker"}, baseline="halt blocked\nhalt blocker\n"
        )
        (tmp_path / vocab_check.HALT_FILE).write_text(
            _halt({"blocked": "blocker", "new_fault": "blocker"}), encoding="utf-8"
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "'new_fault'" in out
        assert "halt type" in out

    def test_new_task_status_fails_and_names_the_value(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            terminal='"completed", "failed"',
            assigned="merged",
            planner='"pending", "completed"',
            baseline=(
                "task_status completed\ntask_status failed\n"
                "task_status merged\ntask_status pending\n"
            ),
        )
        planner = next(
            p for p in vocab_check.TASK_STATUS_SOURCES if p.name == "planner.py"
        )
        (tmp_path / planner).write_text(
            PLANNER_TEMPLATE.format(planner='"pending", "completed", "errored"'),
            encoding="utf-8",
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "'errored'" in out
        assert "task status" in out

    def test_removing_a_value_passes_and_notes_it_for_removal(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        # Record today's full set, then remove a value from the source.
        run_check(tmp_path, baseline, "--update-baseline")
        assert "severity blocker" in baseline.read_text(encoding="utf-8")
        (tmp_path / vocab_check.SEVERITY_FILE).write_text(
            _severity("pass", "warn"), encoding="utf-8"
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0
        assert "'blocker'" in out
        assert "no longer set" in out
        assert "cannot come back" in out

    def test_no_change_is_green(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        run_check(tmp_path, baseline, "--update-baseline")
        code, out = run_check(tmp_path, baseline)
        assert code == 0
        assert "Vocabulary check OK" in out


# ---------------------------------------------------------------------------
# A moved or changed source fails loudly, never enforces less
# ---------------------------------------------------------------------------


class TestSourceShapeFailures:
    def test_missing_severity_field_fails(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        (tmp_path / vocab_check.SEVERITY_FILE).write_text(
            "class ValidatorResult:\n    pass\n", encoding="utf-8"
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "severity field moved" in out

    def test_halt_map_not_a_dict_literal_fails(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        (tmp_path / vocab_check.HALT_FILE).write_text(
            "_CANONICAL_HALT = dict(built_at_import=True)\n", encoding="utf-8"
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "halt map moved" in out

    def test_renamed_task_status_anchor_fails(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        task_cmd = next(
            p for p in vocab_check.TASK_STATUS_SOURCES if p.name == "task_cmd.py"
        )
        (tmp_path / task_cmd).write_text(
            "_RENAMED_STATUSES = {\"completed\"}\n", encoding="utf-8"
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "anchor" in out


# ---------------------------------------------------------------------------
# The update tool cannot launder a new value in
# ---------------------------------------------------------------------------


class TestUpdateBaseline:
    def test_update_refuses_while_a_new_value_is_unrecorded(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            severity='"pass", "warn", "blocker", "info"',
            baseline="severity pass\nseverity warn\nseverity blocker\n",
        )
        code, out = run_check(tmp_path, baseline, "--update-baseline")
        assert code == 1
        assert "Refusing to update" in out
        # The baseline was not widened with the new value.
        assert "severity info" not in baseline.read_text(encoding="utf-8")

    def test_update_seeds_an_empty_baseline_once(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        code, out = run_check(tmp_path, baseline, "--update-baseline")
        assert code == 0
        text = baseline.read_text(encoding="utf-8")
        assert "severity pass" in text
        assert "halt blocked" in text
        assert "task_status completed" in text

    def test_update_drops_a_removed_value(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        run_check(tmp_path, baseline, "--update-baseline")
        (tmp_path / vocab_check.SEVERITY_FILE).write_text(
            _severity("pass", "warn"), encoding="utf-8"
        )
        code, _ = run_check(tmp_path, baseline, "--update-baseline")
        assert code == 0
        assert "severity blocker" not in baseline.read_text(encoding="utf-8")


class TestBaselineFormat:
    def test_malformed_entry_fails_with_the_offending_line(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="bogus severity pass\n")
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "baseline.txt:1" in out

    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        baseline = _make_repo(tmp_path, baseline="")
        run_check(tmp_path, baseline, "--update-baseline")
        original = baseline.read_text(encoding="utf-8")
        baseline.write_text(
            "# a leading comment\n\n" + original + "\n# a trailing comment\n",
            encoding="utf-8",
        )
        report = vocab_check.check(tmp_path, vocab_check.parse_baseline(baseline))
        assert report.failures == []


# ---------------------------------------------------------------------------
# Integration: the real repository is green with its recorded baseline
# ---------------------------------------------------------------------------


def test_real_repository_is_green():
    baseline = vocab_check.parse_baseline(
        REPO_ROOT / vocab_check.BASELINE_RELATIVE_PATH
    )
    report = vocab_check.check(REPO_ROOT, baseline)
    assert report.failures == [], report.failures
    # Today's set is clean; the baseline records every current value.
    for vocabulary, values in report.discovered.items():
        assert values <= baseline[vocabulary]
