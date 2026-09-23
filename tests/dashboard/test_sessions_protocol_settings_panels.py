"""Operator-visible behavior for sessions, protocol, and settings panels."""

import asyncio
import json

from snodo.dashboard.app import SnodoDashboard
from snodo.dashboard.panels import get_panel
from snodo.dashboard.providers import DashboardDataProvider


_PROTOCOL = """\
protocol_id: operator-flow
name: Operator Flow
version: 2.1.0
initial_mode: producer
disagreement_policy: majority
execution:
  max_retries: 5
  branch_ttl_days: 9
  branch_prefix: work
modes:
  - mode_id: producer
    name: Produce
    tools: [read_file, edit]
    validators: [precheck, postcheck]
    coder: mock
    coder_config:
      temperature: 0.2
      max_tokens: 800
    transitions:
      complete: reviewer
  - mode_id: reviewer
    name: Review
    tools: [read_file]
    validators: [postcheck]
validators:
  - validator_id: precheck
    validator_type: security
    evaluation_phase: pre_execute
    severity_cap: warn
    model: review-model
    criteria: [inspect changes]
  - validator_id: postcheck
    validator_type: quality
    evaluation_phase: post_execute
    criteria: [run checks]
global_constraints:
  - constraint_id: protected_api
    description: Keep the public API stable
    expression: "true"
    severity: blocker
"""


def _project(tmp_path, monkeypatch, *, protocol=True, config=None):
    """Create the project files consumed by the real provider/config readers."""
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir(parents=True)
    if protocol:
        (snodo_dir / "protocol.yml").write_text(_PROTOCOL)
    (snodo_dir / "state.json").write_text(json.dumps({
        "current_mode": "producer",
        "active_session": {},
        "metadata": {},
    }))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SNODO_HOME", str(home))
    if config is not None:
        (home / "config.yml").write_text(config)
    return tmp_path, home


def _grid(table):
    return [[str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)]


async def _push(app, panel_id, project):
    screen = get_panel(panel_id, DashboardDataProvider(str(project)))
    app.push_screen(screen)
    await asyncio.sleep(0.15)
    return screen


def test_sessions_panel_shows_empty_list_and_project_header(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch)

    async def run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "sessions", project)
            table = screen.query_one("#session-table")
            assert table.row_count == 0
            header = str(screen.query_one("#session-header").render())
            assert project.name in header
            assert "active" in header and "sessions: 0" in header

    asyncio.run(run())


def test_sessions_panel_lists_real_sessions_and_skips_corrupt_record(tmp_path, monkeypatch):
    project, home = _project(tmp_path, monkeypatch)
    from snodo.infrastructure.session import SessionManager

    manager = SessionManager()
    older = manager.create_session("producer", str(project))
    newer = manager.create_session("reviewer", str(project))
    sessions_dir = home / "sessions"
    (sessions_dir / "corrupt.json").write_text("{not-json")
    (project / ".snodo" / "state.json").write_text(json.dumps({
        "current_mode": "reviewer",
        "active_session": {"reviewer": newer.session_id},
        "metadata": {},
    }))

    # Keep sorting deterministic while preserving valid SessionState records.
    record = json.loads((sessions_dir / f"{older.session_id}.json").read_text())
    record["updated_at"] = "2000-01-01T00:00:00+00:00"
    (sessions_dir / f"{older.session_id}.json").write_text(json.dumps(record))

    async def run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "sessions", project)
            rows = _grid(screen.query_one("#session-table"))
            assert len(rows) == 2
            assert {row[0] for row in rows} == {older.session_id[-12:], newer.session_id[-12:]}
            assert rows[0][1] == "reviewer"
            assert rows[0][-1] == "[bold green]active[/]"
            assert screen.query_one("#session-table").row_count == 2

    asyncio.run(run())


def test_protocol_panel_renders_modes_validator_phases_and_policy(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch)

    async def run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "protocol", project)
            modes = _grid(screen.query_one("#protocol-modes"))
            assert len(modes) == 2
            assert "producer" in modes[0][0] and "complete→reviewer" in modes[0][0]
            assert modes[0][1:4] == ["read_file, edit", "precheck, postcheck", "mock"]
            assert _grid(screen.query_one("#protocol-pre-val"))[0] == [
                "precheck", "security", "warn", "review-model",
            ]
            assert _grid(screen.query_one("#protocol-post-val"))[0][:2] == [
                "postcheck", "quality",
            ]
            assert "majority" in str(screen.query_one("#protocol-policy").render())
            assert "protected_api" in str(screen.query_one("#protocol-constraints").get_row_at(0))

    asyncio.run(run())


def test_protocol_panel_distinguishes_missing_and_invalid_files(tmp_path, monkeypatch):
    missing, _ = _project(tmp_path / "missing", monkeypatch, protocol=False)
    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    invalid, _ = _project(invalid_root, monkeypatch)
    (invalid / ".snodo" / "protocol.yml").write_text("modes: [oops\n")

    async def run(project, expected):
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "protocol", project)
            overview = str(screen.query_one("#protocol-overview").render())
            assert expected in overview
            if expected.startswith("No protocol"):
                assert "No protocol loaded" in str(screen.query_one("#protocol-header").render())
            else:
                assert "Error:" in str(screen.query_one("#protocol-header").render())
                assert not screen.query_one("#protocol-modes").visible

    asyncio.run(run(missing, "No protocol.yml"))
    asyncio.run(run(invalid, "could not be loaded"))


def test_settings_panel_shows_config_protocol_and_provider_values(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch, config="""\
model: openai/gpt-4.1
engine:
  max_subtask_depth: 6
  max_session_age_days: 14
providers:
  custom:
    api_key: secret-test-value
    default_model: custom/model
""")

    async def run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "settings", project)
            overview = str(screen.query_one("#settings-overview").render())
            assert "openai/gpt-4.1" in overview and "config.yml" in overview
            protocol_rows = _grid(screen.query_one("#settings-protocol-models"))
            assert protocol_rows[0] == ["producer", "mock", "temperature=0.2; max_tokens=800", "precheck:review-model"]
            assert protocol_rows[1][0] == "reviewer"
            recovery = str(screen.query_one("#settings-recovery").render())
            assert "Max retries per task: 5" in recovery
            assert "Max subtask depth: 6" in recovery and "Max session age: 14d" in recovery
            provider_rows = _grid(screen.query_one("#settings-providers"))
            assert ["custom", "—", "[green]✓[/]"] in provider_rows

    asyncio.run(run())


def test_settings_panel_uses_defaults_when_config_is_absent(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch)

    async def run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "settings", project)
            overview = str(screen.query_one("#settings-overview").render())
            assert "claude-sonnet-4-20250514" in overview
            assert "could not be loaded" not in overview
            assert "Max retries per task: 5" in str(screen.query_one("#settings-recovery").render())
            assert _grid(screen.query_one("#settings-providers"))

    asyncio.run(run())


def test_settings_panel_explains_malformed_config_without_crashing(tmp_path, monkeypatch):
    project, _ = _project(tmp_path, monkeypatch, config="model: [broken\n")

    async def run():
        app = SnodoDashboard(project_root=str(project))
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _push(app, "settings", project)
            overview = str(screen.query_one("#settings-overview").render())
            assert "Config could not be loaded" in overview
            assert "config.yml" in overview
            assert "Max retries per task: 5" in str(screen.query_one("#settings-recovery").render())
            assert _grid(screen.query_one("#settings-protocol-models"))[0][0] == "producer"
            assert _grid(screen.query_one("#settings-providers"))[0][1] == "Config unavailable"

    asyncio.run(run())
