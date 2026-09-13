"""Tests for scripts/enforce_docs_coverage.py.

These exercise the ratchet's behaviour on synthetic surfaces and corpora —
what fails, what names the offender, what baselines, what refuses to
re-widen — plus one integration test that the real repository passes with
its recorded baseline.
"""

import contextlib
import importlib.util
import io
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enforce_docs_coverage.py"
_spec = importlib.util.spec_from_file_location("enforce_docs_coverage", SCRIPT_PATH)
docs_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(docs_check)


def _surface_with(
    commands: dict[tuple[str, ...], dict] | None = None,
    tools: set[str] | None = None,
    keys: set[str] | None = None,
) -> docs_check.Surface:
    """A surface built by hand: tests control the invokable world.

    Intermediate group nodes are implied by the leaf paths, as they are by a
    real Typer tree.
    """
    surface = docs_check.Surface()
    surface.commands[()] = {
        "opts": frozenset({"--verbose", "--help"}),
        "hidden": False,
        "group": True,
    }
    for path, info in (commands or {}).items():
        for depth in range(1, len(path)):
            surface.commands.setdefault(
                path[:depth],
                {"opts": frozenset({"--help"}), "hidden": False, "group": True},
            )
        info.setdefault("opts", frozenset({"--help"}))
        info.setdefault("hidden", False)
        surface.commands[path] = info
    surface.tools = set(tools or set())
    surface.config_keys = set(keys or set())
    return surface


GROUP = {"group": True, "hidden": False, "opts": frozenset({"--help"})}
LEAF = {"group": False, "hidden": False, "opts": frozenset({"--help", "--json"})}


def _make_repo(
    tmp_path: Path,
    docs: dict[str, str],
    baseline: str | None = None,
) -> Path:
    """Write the docs corpus; write baseline.txt only when given."""
    for rel, content in docs.items():
        path = tmp_path / "docs" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    baseline_path = tmp_path / "baseline.txt"
    if baseline is not None:
        baseline_path.write_text(baseline, encoding="utf-8")
    return baseline_path


def run_check(tmp_path: Path, baseline_path: Path, *args: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = docs_check.main(
            ["--repo", str(tmp_path), "--baseline", str(baseline_path), *args]
        )
    return code, buf.getvalue()


@pytest.fixture
def fake_cli(monkeypatch):
    """Install a surface factory; call .use() to pick the world per test."""
    holder = {"surface": _surface_with()}

    def collect_cli_commands(surface, repo_root):
        built = holder["surface"]
        surface.commands = dict(built.commands)
        surface.tools = set(built.tools)
        surface.config_keys = set(built.config_keys)
        surface.errors = list(built.errors)

    def collect_mcp_tools(surface, repo_root):
        pass  # the fake CLI collector already filled everything

    def collect_config_keys(surface, repo_root):
        pass

    monkeypatch.setattr(docs_check, "collect_cli_commands", collect_cli_commands)
    monkeypatch.setattr(docs_check, "collect_mcp_tools", collect_mcp_tools)
    monkeypatch.setattr(docs_check, "collect_config_keys", collect_config_keys)

    class Holder:
        def use(self, surface: docs_check.Surface) -> None:
            holder["surface"] = surface

    return Holder()


class TestCoverage:
    def test_new_undocumented_command_fails_and_is_named(
        self, tmp_path, fake_cli
    ):
        fake_cli.use(
            _surface_with(commands={("frobnicate",): dict(LEAF)})
        )
        baseline = _make_repo(tmp_path, {"index.md": "# Docs\n\nnothing here\n"})
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "command frobnicate" in out
        assert "no page" in out
        assert "cannot join the baseline" in out

    def test_documented_command_passes(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with(commands={("frobnicate",): dict(LEAF)}))
        baseline = _make_repo(
            tmp_path, {"index.md": "Run `snodo frobnicate --json` to try it.\n"}
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0
        assert "Docs ratchet OK" in out

    def test_tool_and_config_key_coverage_by_bare_name(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with(tools={"spin_widget"}, keys={"llm.widget.size"}))
        baseline = _make_repo(
            tmp_path, {"tools.md": "`spin_widget` sets `llm.widget.size`.\n"}
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0

    def test_baselined_gap_that_got_filled_leaves_and_cannot_return(
        self, tmp_path, fake_cli
    ):
        fake_cli.use(_surface_with(commands={("job", "archive"): dict(LEAF)}))
        docs = {"runbook.md": "Use `snodo job archive` monthly.\n"}
        baseline = _make_repo(
            tmp_path, docs, baseline="command job archive\n"
        )
        # Filled: passes, but the entry must leave the baseline…
        code, out = run_check(tmp_path, baseline)
        assert code == 0
        assert "now documented" in out
        assert "cannot come back" in out
        code, _ = run_check(tmp_path, baseline, "--update-baseline")
        assert code == 0
        assert "command job archive" not in baseline.read_text(encoding="utf-8")
        # …and re-removing the documentation now fails outright.
        _make_repo(tmp_path, {"runbook.md": "Useless page.\n"})
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "command job archive" in out
        assert "cannot join the baseline" in out

    def test_widened_gap_set_fails(self, tmp_path, fake_cli):
        fake_cli.use(
            _surface_with(
                commands={
                    ("job", "archive"): dict(LEAF),
                    ("job", "prune"): dict(LEAF),
                }
            )
        )
        docs = {"runbook.md": "Use `snodo job archive` monthly.\n"}
        baseline = _make_repo(tmp_path, docs, baseline="command job archive\n")
        # job prune ships undocumented: the gap list would widen — it fails.
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "command job prune" in out
        # And the update tool cannot admit it as new debt.
        code, out = run_check(tmp_path, baseline, "--update-baseline")
        assert code == 1
        assert "Refusing" in out
        assert baseline.read_text(encoding="utf-8") == "command job archive\n"

    def test_first_recording_seeds_the_baseline(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with(commands={("frobnicate",): dict(LEAF)}))
        baseline = _make_repo(tmp_path, {"index.md": "# Docs\n"})
        code, out = run_check(tmp_path, baseline, "--update-baseline")
        assert code == 0
        assert "command frobnicate" in baseline.read_text(encoding="utf-8")
        # The seeded check now passes.
        code, out = run_check(tmp_path, baseline)
        assert code == 0
        assert "1 undocumented gaps baselined" in out

    def test_hidden_commands_need_no_documentation(self, tmp_path, fake_cli):
        fake_cli.use(
            _surface_with(
                commands={("aliasy",): {**LEAF, "hidden": True}}
            )
        )
        baseline = _make_repo(tmp_path, {"index.md": "# Docs\n"})
        code, out = run_check(tmp_path, baseline)
        assert code == 0

    def test_stale_baseline_entry_is_reported_and_dropped(
        self, tmp_path, fake_cli
    ):
        fake_cli.use(_surface_with())
        baseline = _make_repo(tmp_path, {"index.md": "# Docs\n"}, baseline="tool gone\n")
        code, out = run_check(tmp_path, baseline)
        assert code == 0
        assert "no longer exists" in out
        run_check(tmp_path, baseline, "--update-baseline")
        assert "gone" not in baseline.read_text(encoding="utf-8")


class TestDangling:
    def test_documented_reference_to_dead_command_fails_and_is_named(
        self, tmp_path, fake_cli
    ):
        fake_cli.use(_surface_with(commands={("plan",): dict(GROUP)}))
        baseline = _make_repo(
            tmp_path,
            {"runbook.md": "The old way: `snodo plan archive-name myplan`.\n"},
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "snodo plan archive-name" in out
        assert "renamed or removed" in out

    def test_documented_reference_to_removed_flag_fails(self, tmp_path, fake_cli):
        fake_cli.use(
            _surface_with(commands={("run",): {"group": False, "hidden": False, "opts": frozenset({"--mock", "--help"})}})
        )
        baseline = _make_repo(
            tmp_path, {"runbook.md": "Try `snodo run --background mytask`.\n"}
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "flag `--background`" in out
        assert "not an option" in out

    def test_reference_to_moved_config_key_fails(self, tmp_path, fake_cli):
        fake_cli.use(
            _surface_with(
                commands={
                    ("config",): dict(GROUP),
                    ("config", "set"): dict(LEAF),
                },
                keys={"llm.classifier.max_tokens"},
            )
        )
        baseline = _make_repo(
            tmp_path,
            {
                "runbook.md": (
                    "Keep `llm.classifier.max_tokens`; the old "
                    "`snodo config set llm.wave.max_tokens 500` is gone.\n"
                )
            },
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "llm.wave.max_tokens" in out
        assert "not settable" in out

    def test_prose_outside_code_is_not_a_reference(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with())
        baseline = _make_repo(
            tmp_path,
            {
                "architecture.md": (
                    "on the MCP path snodo is one tool provider among\n"
                    "several, and snodo's role is narrow. see `snodo --help`.\n"
                )
            },
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0

    def test_command_output_in_a_fence_is_still_scanned(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with(commands={("run",): dict(LEAF)}))
        baseline = _make_repo(
            tmp_path,
            {
                "runbook.md": (
                    "```bash\n$ snodo frob --zap myspec\n```\n"
                    "`snodo frob` mention\n"
                )
            },
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "snodo frob" in out

    def test_paths_assignments_and_possessives_are_not_invocations(
        self, tmp_path, fake_cli
    ):
        fake_cli.use(_surface_with(commands={("run",): dict(LEAF)}))
        baseline = _make_repo(
            tmp_path,
            {
                "runbook.md": (
                    "edit `~/.snodo/config.yml`, set `snodo='uv run snodo'`,\n"
                    "and note that `snodo's output` never says `snodo: nope`.\n"
                    "code: snodo/cli/commands/run_cmd.py — `snodo run` works.\n"
                )
            },
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0

    def test_decisions_and_specs_are_not_scanned(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with(commands={("frobnicate",): dict(LEAF)}))
        # An ADR may describe a removed command and may stand in for coverage?
        # No: decisions are exempt from coverage, and not scanned at all.
        baseline = _make_repo(
            tmp_path,
            {
                "decisions/001-x.md": "`snodo retired-command` was removed.\n",
                "specs/y.md": "`snodo retired-command` per this old spec.\n",
                "index.md": "Use `snodo frobnicate`.\n",
            },
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0, out

    def test_dead_reference_in_a_decision_record_is_not_flagged(
        self, tmp_path, fake_cli
    ):
        fake_cli.use(_surface_with())
        baseline = _make_repo(
            tmp_path,
            {
                "index.md": "# fine\n",
                "decisions/002-y.md": "We removed `snodo dead-thing` today.\n",
            },
        )
        code, out = run_check(tmp_path, baseline)
        assert code == 0, out


class TestBaselineFormat:
    def test_malformed_entry_fails_with_the_offending_line(self, tmp_path, fake_cli):
        fake_cli.use(_surface_with())
        baseline = _make_repo(tmp_path, {"index.md": "# Docs\n"}, baseline="job archive\n")
        code, out = run_check(tmp_path, baseline)
        assert code == 1
        assert "baseline.txt:1" in out


class TestSurfaceCollection:
    def test_tool_registry_is_read_from_the_literal_dict(self, tmp_path):
        path = tmp_path / "tools.py"
        path.write_text(
            'TOOL_REGISTRY = {\n    "read_file": {"mcp": "workspace"},\n}\n'
            'MODE_TOOL_MAP = {"edit": ["read_file"]}\n',
            encoding="utf-8",
        )
        surface = docs_check.Surface()
        docs_check.collect_mcp_tools_from(surface, path)
        assert surface.tools == {"read_file"}

    def test_missing_registry_is_a_collection_error(self, tmp_path):
        path = tmp_path / "tools.py"
        path.write_text("TOOL_LIST = []\n", encoding="utf-8")
        surface = docs_check.Surface()
        docs_check.collect_mcp_tools_from(surface, path)
        assert surface.errors and "TOOL_REGISTRY" in surface.errors[0]

    def test_config_keys_come_from_the_settable_and_engine_surfaces(self, tmp_path):
        cmd = tmp_path / "config_cmd.py"
        cmd.write_text(
            '''
_LLM_SETTABLE_KEYS = {"num_retries": int, "coder.model": str}


def _config_set(mgr, key, value):
    parts = key.split(".", 1)
    if len(parts) == 2 and parts[0] == "engine":
        engine_key = parts[1]
        if engine_key in ("max_subtask_depth", "token_ttl_seconds"):
            mgr.set_engine_value(engine_key, value)
    elif key == "model":
        mgr.set_model(value)
''',
            encoding="utf-8",
        )
        core = tmp_path / "config.py"
        core.write_text(
            'data.setdefault("engine", {"max_subtask_depth": 3, "extra_knob": 1})\n',
            encoding="utf-8",
        )
        surface = docs_check.Surface()
        docs_check.collect_config_keys_from(surface, cmd, core)
        assert surface.config_keys == {
            "llm.num_retries",
            "llm.coder.model",
            "model",
            "engine.max_subtask_depth",
            "engine.token_ttl_seconds",
            "engine.extra_knob",
        }


class TestRealRepository:
    def test_check_passes_on_the_repository_as_it_stands(self):
        """The ratchet ships green: recorded baseline covers today's debt,
        and today's docs hold no dangling references."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = docs_check.main(["--repo", str(REPO_ROOT)])
        assert code == 0, buf.getvalue()
        assert "Docs ratchet OK" in buf.getvalue()
