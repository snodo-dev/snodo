"""Tests for scripts/enforce_file_length.py.

These exercise the ratchet's behaviour, not just the count: what fails,
what passes with a tightening hint, what leaves the baseline, and what
counts as a line of code.
"""

import contextlib
import importlib.util
import io
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enforce_file_length.py"
_spec = importlib.util.spec_from_file_location("enforce_file_length", SCRIPT_PATH)
length_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(length_check)

LIMIT_ARGS = ("--limit", "100")


def _code_file(loc: int) -> str:
    """A .py file with exactly ``loc`` lines of code (one assignment each)."""
    return "".join(f"x_{i} = {i}\n" for i in range(loc))


def _make_repo(tmp_path: Path, files: dict[str, str], baseline: str = "") -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(baseline, encoding="utf-8")
    return baseline_path


def run_check(tmp_path: Path, baseline_path: Path, *args: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = length_check.main(
            ["--repo", str(tmp_path), "--baseline", str(baseline_path), *args]
        )
    return code, buf.getvalue()


class TestCountLoc:
    def test_blank_and_comment_lines_are_not_code(self):
        source = "# a comment\n\nx = 1\n\n# another\ny = 2\n"
        assert length_check.count_loc(source) == 2

    def test_docstring_lines_are_not_code(self):
        source = '"""Module doc.\n\nMany lines of prose.\n"""\n\n\ndef f():\n    """Doc.\n\n    Prose.\n    """\n    return 1\n'
        assert length_check.count_loc(source) == 2  # `def f():` and `return 1`

    def test_multi_line_statement_counts_every_physical_line(self):
        source = "result = call(\n    a,\n    b,\n)\n"
        assert length_check.count_loc(source) == 4

    def test_fstring_spans_are_code(self):
        source = 'x = f"""a\nb\nc"""\n'
        assert length_check.count_loc(source) == 3

    def test_unparseable_source_raises(self):
        with pytest.raises(ValueError):
            length_check.count_loc("def f(:\n")


class TestRatchet:
    def test_file_over_limit_without_baseline_fails(self, tmp_path):
        baseline = _make_repo(
            tmp_path, {"big.py": _code_file(101)}, baseline=""
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 1
        assert "big.py" in out
        assert "over the 100 limit by 1" in out
        assert "not in the baseline" in out

    def test_baselined_file_that_grew_by_one_fails_and_names_baseline(
        self, tmp_path
    ):
        baseline = _make_repo(
            tmp_path,
            {"big.py": _code_file(101)},
            baseline="100 big.py\n",
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 1
        assert "big.py" in out
        assert "baseline is 100" in out
        assert "grown by 1" in out
        assert "may only fall" in out

    def test_baselined_file_that_shrank_passes_and_says_it_can_tighten(
        self, tmp_path
    ):
        baseline = _make_repo(
            tmp_path,
            {"big.py": _code_file(105)},
            baseline="110 big.py\n",
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 0
        assert "tighten" in out
        assert "down 5 from its baseline of 110" in out

    def test_file_below_limit_is_ready_to_leave_the_baseline(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            {"big.py": _code_file(90)},
            baseline="110 big.py\n",
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 0
        assert "remove it from the baseline" in out
        assert "cannot come back" in out

    def test_comment_heavy_file_passes_despite_physical_length(self, tmp_path):
        docstring = '"""\n' + ("prose explanation of the recovery logic\n" * 200) + '"""\n'
        comments = "# more deliberate prose, the reason a reader can follow this\n" * 300
        source = docstring + comments + _code_file(50)
        assert len(source.splitlines()) > 550
        baseline = _make_repo(tmp_path, {"prosy.py": source}, baseline="")
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 0
        assert "Ratchet OK" in out

    def test_tests_directories_are_not_checked(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            {"tests/test_big.py": _code_file(500), "pkg/tests/extra.py": _code_file(500)},
            baseline="",
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 0
        assert "tests" not in out.split("Ratchet OK")[0]

    def test_deleted_baselined_file_is_reported_for_removal(self, tmp_path):
        baseline = _make_repo(tmp_path, {"kept.py": "x = 1\n"}, baseline="110 gone.py\n")
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 0
        assert "gone.py" in out
        assert "no longer exists" in out


class TestUpdateBaseline:
    def test_update_tightens_shrinks_and_drops_exited_files(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            {
                "one.py": _code_file(105),
                "two.py": _code_file(90),
            },
            baseline="110 one.py\n110 two.py\n",
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS, "--update-baseline")
        assert code == 0
        text = baseline.read_text(encoding="utf-8")
        assert "one.py" in text
        assert "105 one.py" in text
        assert "two.py" not in text  # under the limit: gone for good

    def test_update_refuses_while_anything_is_over_its_baseline(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            {"big.py": _code_file(111)},
            baseline="110 big.py\n",
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS, "--update-baseline")
        assert code == 1
        assert "baseline is 110" in out
        # The ratchet was not loosened.
        assert "110 big.py" in baseline.read_text(encoding="utf-8")

    def test_update_cannot_admit_a_new_over_limit_file(self, tmp_path):
        baseline = _make_repo(tmp_path, {"new.py": _code_file(101)}, baseline="")
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS, "--update-baseline")
        assert code == 1
        assert "not in the baseline" in out
        assert baseline.read_text(encoding="utf-8") == ""


class TestBaselineFormat:
    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        baseline = _make_repo(
            tmp_path,
            {"big.py": _code_file(105)},
            baseline="# debt list\n\n110 big.py\n  # indented comment\n",
        )
        code, _ = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 0

    def test_malformed_entry_fails_with_the_offending_line(self, tmp_path):
        baseline = _make_repo(
            tmp_path, {"small.py": "x = 1\n"}, baseline="big.py 110\n"
        )
        code, out = run_check(tmp_path, baseline, *LIMIT_ARGS)
        assert code == 1
        assert "baseline.txt:1" in out
