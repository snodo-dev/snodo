"""Branch-coverage tests for snodo/engine/nodes/context.py.

Targets missing lines:
  32-46  — _maybe_summarize with _summary_model present (success + exception fallthrough)
  116-117 — _build_dir_tree outer except (FileNotFoundError, ValueError): continue
"""

import pytest
from unittest.mock import MagicMock

from snodo.compiler.models import Protocol, Mode, Validator
from snodo.core.interfaces import Task
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import LoopState


def _make_protocol():
    return Protocol(
        protocol_id="test", name="Test",
        modes=[Mode(mode_id="producer", name="Producer", tools=[], validators=[])],
        validators=[Validator(validator_id="v1", validator_type="security",
                              evaluation_phase="pre_execute")],
        initial_mode="producer",
    )


def _big_messages(n=10, char_size=5000):
    """Generate messages whose total char count >> 32000 (>8000 token estimate)."""
    return [{"role": "user", "content": "x" * char_size} for _ in range(n)]


# ---------------------------------------------------------------------------
# _maybe_summarize
# ---------------------------------------------------------------------------

class TestMaybeSummarizeWithModel:
    def test_summary_model_invoked_on_large_messages(self):
        """Lines 32-46: _summary_model.invoke called, summary set, messages truncated to 3."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summarized history: key decisions made."
        mock_model.invoke.return_value = mock_response
        builder._summary_model = mock_model

        state = LoopState(
            task=Task(id="t1", spec="test"),
            current_mode="producer",
            messages=_big_messages(),
            summary="",
        )

        result = builder._maybe_summarize(state)

        mock_model.invoke.assert_called_once()
        assert result.summary == "Summarized history: key decisions made."
        assert len(result.messages) == 3

    def test_summary_model_exception_falls_through_to_truncation(self):
        """Lines 32-46 + 48-53: model.invoke raises → falls through to truncation fallback."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)

        mock_model = MagicMock()
        mock_model.invoke.side_effect = RuntimeError("API down")
        builder._summary_model = mock_model

        msgs = _big_messages()
        state = LoopState(
            task=Task(id="t1", spec="test"),
            current_mode="producer",
            messages=msgs,
            summary="",
        )

        result = builder._maybe_summarize(state)

        # Falls through to truncation path
        assert len(result.messages) == 3
        assert result.summary.startswith("Previous:")

    def test_below_threshold_no_invoke(self):
        """Short messages: summary model NOT called."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)

        mock_model = MagicMock()
        builder._summary_model = mock_model

        state = LoopState(
            task=Task(id="t1", spec="test"),
            current_mode="producer",
            messages=[{"role": "user", "content": "short"}],
            summary="",
        )
        result = builder._maybe_summarize(state)
        mock_model.invoke.assert_not_called()
        assert len(result.messages) == 1

    def test_no_model_truncation_empty_discarded(self):
        """Fewer than 4 messages above threshold but no model → summary stays empty."""
        protocol = _make_protocol()
        builder = GraphBuilder(protocol)
        builder._summary_model = None

        # 2 big messages: discarded = messages[:-3] = [] (no discarded), no snippet
        state = LoopState(
            task=Task(id="t1", spec="test"),
            current_mode="producer",
            messages=[
                {"role": "user", "content": "x" * 16000},
                {"role": "assistant", "content": "y" * 16000},
            ],
            summary="",
        )
        result = builder._maybe_summarize(state)
        assert len(result.messages) == 2
        assert result.summary == ""


# ---------------------------------------------------------------------------
# _build_dir_tree — outer FileNotFoundError/ValueError on list_files(current_path)
# ---------------------------------------------------------------------------

class TestBuildDirTreeExceptions:
    def _fake_workspace_raises_on_root(self):
        """list_files raises FileNotFoundError on root '.' → outer except continue."""
        ws = MagicMock()
        ws.list_files.side_effect = FileNotFoundError("no such dir")
        return ws

    def test_outer_list_files_raises_continue(self):
        """Lines 116-117: list_files(current_path) raises → continue, returns empty string."""
        ws = self._fake_workspace_raises_on_root()
        result = GraphBuilder._build_dir_tree(ws, max_depth=3)
        assert result == ""

    def test_outer_list_files_value_error_continue(self):
        """ValueError on list_files → continue without crashing."""
        ws = MagicMock()
        ws.list_files.side_effect = ValueError("bad path")
        result = GraphBuilder._build_dir_tree(ws, max_depth=3)
        assert result == ""

    def test_mixed_files_and_dirs(self):
        """Inner except: list_files(child_path) raises → treated as file (no trailing slash)."""
        ws = MagicMock()

        def list_files(path):
            if path == ".":
                return ["mydir", "myfile.txt"]
            elif path == "mydir":
                return ["inner.txt"]
            elif path == "myfile.txt":
                raise FileNotFoundError("not a dir")
            elif path == "mydir/inner.txt":
                raise FileNotFoundError("not a dir")
            return []

        ws.list_files.side_effect = list_files
        result = GraphBuilder._build_dir_tree(ws, max_depth=3)
        assert "mydir/" in result
        assert "myfile.txt" in result
        assert "myfile.txt/" not in result

    def test_hidden_entries_skipped(self):
        """Entries starting with '.' or matching noise names are skipped."""
        ws = MagicMock()

        def list_files(path):
            if path == ".":
                return [".git", "__pycache__", "node_modules", "src"]
            elif path == "src":
                return []
            return []

        ws.list_files.side_effect = list_files
        result = GraphBuilder._build_dir_tree(ws, max_depth=3)
        assert ".git" not in result
        assert "__pycache__" not in result
        assert "node_modules" not in result
        assert "src/" in result
