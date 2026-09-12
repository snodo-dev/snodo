"""A retry must not destroy the thing it retries.

FILE: tests/cli/test_retry_spec_safety.py

The recorded failure: a task whose sixty-line specification was the product of
serious work failed for an *operational* reason. The follow-up command snodo
printed was

    snodo run --retry <task_id> "revised spec"

and pasting it replaced that specification with the two words in the placeholder,
because the positional argument was both the only way to say anything about a
spec and the replacing kind by default. Two attempts later the meta-spec
validator reported the task as underspecified — correctly, against a spec that
no longer existed anywhere the operator could reach.

These tests pin the three shapes a retry can take (bare, additive, replacing),
that the command snodo *prints* is the bare one, that a pasted suggestion
therefore preserves the spec, and that a deliberate replacement keeps the
discarded specification recoverable.
"""

import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


from snodo.cli.commands import followup
from snodo.cli.commands.run_cmd import RunArgs, _retry_task
from snodo.infrastructure.audit import AuditLog
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import ProjectState, write_state
from snodo.protocols import _TEMPLATE_PROTOCOLS

LONG_SPEC = "\n".join(
    [
        "Add a rate limiter to the public ingestion endpoint.",
        "",
        "Constraints:",
        "- token bucket, 100 req/min per API key, refilling continuously",
        "- reject with 429 and a Retry-After header",
        "- no new dependencies; reuse the existing redis client",
        "- the limiter must be constructed once per worker, not per request",
        "- cover: under limit, over limit, boundary at exactly the limit",
        "- document the header behaviour in docs/api.md",
    ]
)


_PROTOCOL_YAML = (
    "protocol_id: \"test\"\nname: \"Test\"\nversion: \"1.0.0\"\n"
    "modes:\n  - mode_id: \"producer\"\n    name: \"Producer\"\n"
    "    tools: [\"edit\"]\n    validators: [\"security\"]\n"
    "    transitions: {}\n"
    "validators:\n  - validator_id: \"security\"\n"
    "    validator_type: \"security\"\n"
    "    evaluation_phase: \"pre_execute\"\n    criteria: [\"check\"]\n"
    "disagreement_policy: \"unanimous\"\ninitial_mode: \"producer\"\n"
    "global_constraints: []\n"
)


def _real_cli_project(tmp_path, monkeypatch, task_id="task_abc", spec=LONG_SPEC, attempt=1):
    """A project the *real* CLI can run against, with one recorded failure.

    Returns (session_manager, session, audit_log); the caller imports
    ``snodo.cli.main`` and pastes commands through it.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SNODO_HOME", str(home))
    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(str(tmp_path), ProjectState(current_mode=mode))
    _write_protocol(tmp_path)

    mgr = SessionManager()
    session = mgr.create_session(mode, str(tmp_path))
    _record_failure(mgr, session, task_id, spec, attempt=attempt)
    audit = AuditLog(str(tmp_path / ".snodo" / "audit.log"))
    monkeypatch.chdir(tmp_path)
    return mgr, session, audit


def _paste(main, command):
    """Run a printed suggestion verbatim, the way an operator pastes it."""
    executed = []
    with patch("snodo.cli.commands.run_cmd._execute_task",
               side_effect=lambda a, p, t, m: executed.append(t) or 0):
        code = main(shlex.split(command)[1:])
    assert code == 0, f"{command} exited {code}"
    assert len(executed) == 1
    return executed[0]


def _write_protocol(project_root):
    """Give the project a protocol file the real CLI can load."""
    snodo_dir = Path(project_root) / ".snodo"
    snodo_dir.mkdir(exist_ok=True)
    (snodo_dir / "protocol.yml").write_text(_PROTOCOL_YAML)


def _setup(tmp_path, monkeypatch):
    """A project with one active session; _execute_task records what it was given."""
    project_root = str(tmp_path)
    protocol = _TEMPLATE_PROTOCOLS["solo"]
    mode = protocol.modes[0].mode_id
    write_state(project_root, ProjectState(current_mode=mode))

    session_mgr = SessionManager(sessions_dir=tmp_path / ".snodo" / "sessions")
    session = session_mgr.create_session(mode, project_root)

    monkeypatch.setattr("snodo.cli.commands.run_cmd.load_protocol", lambda path: protocol)

    executed = []

    def mock_execute_task(args, prot, t, m):
        executed.append(t)
        return 0

    monkeypatch.setattr("snodo.cli.commands.run_cmd._execute_task", mock_execute_task)

    audit_path = tmp_path / ".snodo" / "audit.log"
    audit_log = AuditLog(str(audit_path))

    return project_root, session_mgr, session, executed, audit_log


def _record_failure(session_mgr, session, task_id, spec, attempt=1):
    session_mgr.update_decision(session.session_id, "task_failure", {
        task_id: {
            "spec": spec,
            "original_spec": spec,
            "branch": f"task/{task_id}/add-a-rate-limiter",
            "attempt": attempt,
            "phase": "execute",
            "failed_validators": [
                {
                    "validator_id": "execution_error",
                    "severity": "blocker",
                    "justification": "provider rejected the request: missing header",
                }
            ],
            "files_changed": [],
        }
    })


def _args(audit_log=None, **overrides):
    base = dict(
        protocol=".snodo/protocol.yml",
        model="mock-model",
        description=None,
        append_spec=None,
        replace_spec=None,
        audit_log=audit_log,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# === bare retry: the default, and the one that changes nothing ===

def test_bare_retry_preserves_the_original_spec(tmp_path, monkeypatch):
    """A retry given no spec text re-runs the task against the spec on record."""
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    assert _retry_task(_args(), "task_rate", project_root, mgr) == 0
    task = executed[0]

    assert task.root_spec == LONG_SPEC
    # The operational reason is still handed to the coder...
    assert "provider rejected the request: missing header" in task.spec
    # ...but nothing claims the spec changed.
    assert "Revised spec" not in task.spec
    assert "Added guidance" not in task.spec
    assert task.spec.startswith(f"Original spec: {LONG_SPEC}")


def test_bare_retry_does_not_record_a_superseded_spec(tmp_path, monkeypatch):
    """Nothing was discarded, so nothing is booked as replaced."""
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    _retry_task(_args(), "task_rate", project_root, mgr)

    entry = mgr.load_session(session.session_id).checkpoint.decisions["task_failure"]["task_rate"]
    assert "superseded_spec" not in entry
    assert not audit.get_history("spec_replaced")


# === additive: guidance on top of what is already written ===

def test_append_spec_adds_guidance_without_losing_the_spec(tmp_path, monkeypatch):
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    args = _args(append_spec="also note: the redis key prefix is set in config.py")
    assert _retry_task(args, "task_rate", project_root, mgr) == 0
    task = executed[0]

    assert task.root_spec == f"{LONG_SPEC}\n\nalso note: the redis key prefix is set in config.py"
    assert "Added guidance for this attempt" in task.spec
    assert "Revised spec (replaces original)" not in task.spec
    # The guidance rides on top of the spec; the spec is still there in full.
    assert LONG_SPEC in task.root_spec


def test_positional_text_with_retry_is_guidance_not_a_replacement(tmp_path, monkeypatch):
    """The shape that destroyed the spec can no longer destroy it.

    A bare ``snodo run --retry <id> "text"`` is the old invocation. Its text is
    now read as guidance added to the recorded spec: the destructive reading is
    only available through ``--replace-spec``, which names itself as such.
    """
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    assert _retry_task(_args(audit_log=audit, description="retry now"), "task_rate", project_root, mgr) == 0
    task = executed[0]

    assert task.root_spec == f"{LONG_SPEC}\n\nretry now"
    assert "Revised spec (replaces original)" not in task.spec
    assert not audit.get_history("spec_replaced")


def test_guidance_that_repeats_the_spec_is_a_bare_retry(tmp_path, monkeypatch):
    """Handing back the recorded spec changes nothing and is not a revision."""
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    _retry_task(_args(audit_log=audit, replace_spec=f"  {LONG_SPEC}  "), "task_rate", project_root, mgr)
    task = executed[0]

    assert task.root_spec == LONG_SPEC
    assert "Revised spec" not in task.spec
    assert not audit.get_history("spec_replaced")


def test_appended_guidance_that_repeats_the_spec_adds_nothing(tmp_path, monkeypatch):
    """The additive form obeys the same rule: an echo of the spec is not guidance."""
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    _retry_task(_args(append_spec=LONG_SPEC), "task_rate", project_root, mgr)
    task = executed[0]

    assert task.root_spec == LONG_SPEC
    assert "Added guidance" not in task.spec


def test_a_failing_recovery_record_does_not_stop_the_retry(tmp_path, monkeypatch, caplog):
    """Keeping the discarded spec is a safeguard, not a gate: if either copy
    cannot be written the retry still runs, with the loss said out loud."""
    import logging

    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    real_update = mgr.update_decision

    def _refuse_failure_writes(session_id, key, value):
        if key == "task_failure":
            raise RuntimeError("session store down")
        return real_update(session_id, key, value)

    monkeypatch.setattr(mgr, "update_decision", _refuse_failure_writes)
    monkeypatch.setattr(
        audit, "append_event",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit chain busy")),
    )

    with caplog.at_level(logging.WARNING, logger="snodo.cli.commands.run_cmd"):
        assert _retry_task(
            _args(audit_log=audit, replace_spec="a replacement"), "task_rate", project_root, mgr
        ) == 0

    assert executed[0].root_spec == "a replacement"
    warnings = " ".join(record.getMessage() for record in caplog.records)
    assert "Could not record superseded spec" in warnings
    assert "Could not audit spec_replaced" in warnings


# === replacing: available, deliberate, and recoverable ===

def test_replace_spec_replaces_and_keeps_the_old_one(tmp_path, monkeypatch):
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    args = _args(audit_log=audit, replace_spec="drop the limiter; document the endpoint instead")
    assert _retry_task(args, "task_rate", project_root, mgr) == 0
    task = executed[0]

    assert task.root_spec == "drop the limiter; document the endpoint instead"
    assert "Revised spec (replaces original): drop the limiter" in task.spec

    # The discarded spec survives in both places an operator can reach: the
    # session record (snodo task show) and the append-only audit log.
    entry = mgr.load_session(session.session_id).checkpoint.decisions["task_failure"]["task_rate"]
    assert entry["superseded_specs"] == [LONG_SPEC]
    assert entry["superseded_spec"] == LONG_SPEC
    events = audit.get_history("spec_replaced")
    assert events[0].data["previous_spec"] == LONG_SPEC
    assert events[0].data["new_spec"] == "drop the limiter; document the endpoint instead"


def test_replaced_spec_survives_a_second_failure(tmp_path, monkeypatch):
    """The engine rewrites the failure record every attempt; the superseded copy
    must be carried forward rather than dropped with the old spec."""
    from snodo.core.interfaces import Task
    from snodo.engine.nodes.writeback import WritebackMixin

    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)
    _retry_task(_args(audit_log=audit, replace_spec="a shorter second attempt"), "task_rate", project_root, mgr)

    class _WB(WritebackMixin):
        pass

    wb = _WB()
    wb._session_manager = mgr
    wb._session_id = session.session_id
    wb.coder = None
    wb._default_model = "mock-model"
    wb._job_id = None

    state = SimpleNamespace(
        task=Task(id="task_rate", spec="augmented prompt", root_spec="a shorter second attempt"),
        artifacts=[],
        constraint_violations=["still failing"],
        halt_type="internal_error",
        pending_disagreement=None,
        iteration=1,
        current_mode="producer",
        is_blocked=True,
        validation_results=[],
        policy_decision=None,
        metadata={"post_validation": None},
    )
    wb._auto_write_failure_context(state, [])

    entry = mgr.load_session(session.session_id).checkpoint.decisions["task_failure"]["task_rate"]
    assert entry["original_spec"] == "a shorter second attempt"
    assert entry["superseded_specs"] == [LONG_SPEC]


def test_repeated_additive_retries_accumulate_without_wrapping(tmp_path, monkeypatch):
    """Guidance joins the spec once per attempt and the header never nests.

    The second attempt is told about the first attempt's guidance as part of the
    specification — which is what it now is — not as a second wrapper around it.
    """
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", "the first spec")

    _retry_task(_args(append_spec="note one"), "task_rate", project_root, mgr)
    assert executed[0].root_spec == "the first spec\n\nnote one"

    # The next failure carries the combined spec as its original spec.
    _record_failure(mgr, session, "task_rate", "the first spec\n\nnote one", attempt=2)
    _retry_task(_args(append_spec="note two"), "task_rate", project_root, mgr)
    task = executed[-1]

    assert task.root_spec == "the first spec\n\nnote one\n\nnote two"
    assert task.spec.count("Original spec:") == 1
    assert task.spec.count("Added guidance for this attempt") == 1


def test_replaced_spec_survives_a_replacement_of_a_replacement(tmp_path, monkeypatch):
    """Two replacements keep both discarded specs, oldest first."""
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", "spec one")
    _retry_task(_args(audit_log=audit, replace_spec="spec two"), "task_rate", project_root, mgr)

    entry = mgr.load_session(session.session_id).checkpoint.decisions["task_failure"]["task_rate"]
    entry["attempt"] = 2
    mgr.update_decision(session.session_id, "task_failure", {"task_rate": entry})

    _retry_task(_args(audit_log=audit, replace_spec="spec three"), "task_rate", project_root, mgr)

    entry = mgr.load_session(session.session_id).checkpoint.decisions["task_failure"]["task_rate"]
    assert entry["superseded_specs"] == ["spec one", "spec two"]
    assert executed[-1].root_spec == "spec three"


def test_task_show_prints_the_superseded_spec(tmp_path, monkeypatch, capsys):
    """Recovery is one command deep, and it is a command the CLI already names."""
    from snodo.cli.commands.task_cmd import task_show_command

    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)
    _retry_task(_args(audit_log=audit, replace_spec="something shorter"), "task_rate", project_root, mgr)
    capsys.readouterr()

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: project_root)
    with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
        assert task_show_command(SimpleNamespace(task_id="task_rate", json=False)) == 0

    out = capsys.readouterr().out
    assert "Superseded spec" in out
    assert "Add a rate limiter to the public ingestion endpoint." in out
    # The recovery command is one the CLI prints, not one the operator has to
    # invent from a job payload.
    assert "snodo task show task_rate --json" in out


def test_task_show_json_carries_the_superseded_spec(tmp_path, monkeypatch, capsys):
    from snodo.cli.commands.task_cmd import task_show_command

    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)
    _retry_task(_args(audit_log=audit, replace_spec="something shorter"), "task_rate", project_root, mgr)
    capsys.readouterr()

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: project_root)
    with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
        assert task_show_command(SimpleNamespace(task_id="task_rate", json=True)) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["failure"]["superseded_specs"] == [LONG_SPEC]


# === the shapes cannot be confused with each other ===

def test_append_and_replace_together_is_refused(tmp_path, monkeypatch, capsys):
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    args = _args(append_spec="guidance", replace_spec="replacement", retry="task_rate")
    assert _retry_task(args, "task_rate", project_root, mgr) == 1
    assert executed == []
    assert "not both" in capsys.readouterr().err


def test_replace_spec_with_positional_text_is_refused(tmp_path, monkeypatch, capsys):
    """The positional is guidance; combining it with a replacement is ambiguous."""
    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    _record_failure(mgr, session, "task_rate", LONG_SPEC)

    args = _args(description="guidance", replace_spec="replacement", retry="task_rate")
    assert _retry_task(args, "task_rate", project_root, mgr) == 1
    assert executed == []


def test_spec_flags_without_a_retry_are_refused(tmp_path, monkeypatch, capsys):
    from snodo.cli.commands.run_cmd import _retry_spec_conflict

    error = _retry_spec_conflict(RunArgs(append_spec="guidance"))
    assert error and "only apply to a retry" in error
    error = _retry_spec_conflict(RunArgs(retry="task_rate", replace_spec="replacement"))
    assert error is None


# === the same three shapes on `snodo job retry` ===

class TestJobRetrySurface:
    """`snodo job retry <job_id>` drives the same retry, so it reads the same way."""

    def _with_job(self, tmp_path, monkeypatch):
        mgr, session, audit = _real_cli_project(
            tmp_path, monkeypatch, task_id="task_rate",
        )
        job_dir = tmp_path / ".snodo" / "jobs" / "j_rate"
        job_dir.mkdir(parents=True)
        (job_dir / "task.json").write_text(
            json.dumps({"task_id": "task_rate", "description": LONG_SPEC})
        )
        from snodo.cli.main import main
        return main, audit

    def test_bare_job_retry_keeps_the_spec(self, tmp_path, monkeypatch):
        main, audit = self._with_job(tmp_path, monkeypatch)
        task = _paste(main, "snodo job retry j_rate")
        assert task.root_spec == LONG_SPEC
        assert "Revised spec" not in task.spec

    def test_job_retry_description_is_guidance(self, tmp_path, monkeypatch):
        main, audit = self._with_job(tmp_path, monkeypatch)
        task = _paste(main, 'snodo job retry j_rate "add the missing header"')
        assert task.root_spec == f"{LONG_SPEC}\n\nadd the missing header"
        assert "Revised spec (replaces original)" not in task.spec

    def test_job_retry_replace_spec_replaces(self, tmp_path, monkeypatch):
        main, audit = self._with_job(tmp_path, monkeypatch)
        task = _paste(
            main,
            'snodo job retry j_rate --replace-spec "start over, smaller"',
        )
        assert task.root_spec == "start over, smaller"
        from snodo.infrastructure.state import read_state
        session = SessionManager().get_active_session(
            read_state(str(tmp_path)).current_mode, str(tmp_path)
        )
        entry = session.checkpoint.decisions["task_failure"]["task_rate"]
        assert entry["superseded_specs"] == [LONG_SPEC]


# === what snodo prints ===

class TestPrintedFollowUpIsSafeToPaste:
    def test_suggested_retry_carries_no_spec_argument(self):
        """The suggestion is a bare retry: nothing in it can rewrite a spec."""
        suggestion = followup.task_retry("task_abc")
        assert suggestion == "snodo run --retry task_abc"
        tokens = shlex.split(suggestion)
        assert "--append-spec" not in tokens and "--replace-spec" not in tokens

    def test_pasting_the_suggested_retry_preserves_the_spec(self, tmp_path, monkeypatch):
        from snodo.cli.main import main

        mgr, session, audit = _real_cli_project(tmp_path, monkeypatch)
        task = _paste(main, followup.task_retry("task_abc"))
        assert task.root_spec == LONG_SPEC
        entry = mgr.load_session(session.session_id).checkpoint.decisions["task_failure"]["task_abc"]
        assert "superseded_spec" not in entry

    def test_pasting_the_annotate_suggestion_keeps_the_spec(self, tmp_path, monkeypatch):
        """Even the printed alternative is paste-safe: it adds, it does not erase."""
        from snodo.cli.main import main

        _real_cli_project(tmp_path, monkeypatch)
        task = _paste(main, followup.task_retry_annotate("task_abc"))
        assert task.root_spec.startswith(LONG_SPEC)
        assert "Revised spec (replaces original)" not in task.spec

    def test_pasting_the_replace_suggestion_is_explicitly_a_replacement(
        self, tmp_path, monkeypatch
    ):
        """The replacing shape says so in its own flag name, and keeps the copy."""
        from snodo.cli.main import main

        _real_cli_project(tmp_path, monkeypatch)
        task = _paste(main, followup.task_retry_replace("task_abc"))
        assert task.root_spec == "replacement spec"
        assert task.root_spec != LONG_SPEC

    def test_run_append_spec_flag_reaches_the_retry(self, tmp_path, monkeypatch):
        from snodo.cli.main import main

        _real_cli_project(tmp_path, monkeypatch)
        task = _paste(main, 'snodo run --retry task_abc --append-spec "watch the header"')
        assert task.root_spec == f"{LONG_SPEC}\n\nwatch the header"

    def test_run_replace_spec_flag_reaches_the_retry(self, tmp_path, monkeypatch):
        from snodo.cli.main import main

        _real_cli_project(tmp_path, monkeypatch)
        task = _paste(main, 'snodo run --retry task_abc --replace-spec "brand new spec"')
        assert task.root_spec == "brand new spec"

    def test_two_spec_shapes_on_the_cli_are_refused(self, tmp_path, monkeypatch, capsys):
        """Typer must let the contradictory pair reach the guard that refuses it."""
        from snodo.cli.main import main

        _real_cli_project(tmp_path, monkeypatch)
        code = main([
            "run", "--retry", "task_abc",
            "--append-spec", "guidance", "--replace-spec", "replacement",
        ])
        assert code == 1
        assert "not both" in capsys.readouterr().err

    def test_the_restore_hint_cannot_be_pasted_into_a_destruction(self, tmp_path, monkeypatch):
        """The restore line names the option without a value: pasting it asks for
        the text instead of overwriting the live spec with a placeholder."""
        from snodo.cli.main import main

        _real_cli_project(tmp_path, monkeypatch)
        executed = []
        with patch("snodo.cli.commands.run_cmd._execute_task",
                   side_effect=lambda a, p, t, m: executed.append(t) or 0):
            code = main(shlex.split(followup.task_retry_restore("task_abc"))[1:])
        assert code != 0
        assert executed == []

    def test_revision_options_are_printed_as_options_not_as_the_suggestion(self):
        """The deliberate shapes exist in the exhausted-retry guidance, named by
        what they do to the spec, and listed after the unchanged-spec retry."""
        options = followup.task_retry_options("task_abc")
        assert options[0].startswith("snodo run --retry task_abc (")
        assert not options[0].startswith('snodo run --retry task_abc "')
        assert any("--append-spec" in o for o in options)
        assert any("--replace-spec" in o for o in options)

    def test_exhausted_retry_prints_the_bare_retry_first(self, tmp_path, monkeypatch, capsys):
        project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
        _record_failure(mgr, session, "task_rate", LONG_SPEC, attempt=3)

        assert _retry_task(_args(), "task_rate", project_root, mgr) == 1
        out = capsys.readouterr().out
        assert "snodo run --retry task_rate" in out
        assert '"revised spec"' not in out


# === what the review excerpt and older records show ===

def test_unwrap_spec_stops_at_the_added_guidance():
    """An excerpt of a retried task's record shows the specification, not the
    note added beside it."""
    from snodo.cli.commands.task_cmd import _unwrap_spec

    wrapped = (
        f"Original spec: {LONG_SPEC}\n\n"
        "Added guidance for this attempt (the spec above still stands): mind the header\n\n"
        "Previous attempt 1 failed at execute:\n"
        "  execution_error: provider rejected the request\n\n"
        "Fix the issues above."
    )
    assert _unwrap_spec(wrapped) == LONG_SPEC


def test_task_show_reads_a_legacy_superseded_spec(tmp_path, monkeypatch, capsys):
    """A record written before the history list existed is still recoverable."""
    from snodo.cli.commands.task_cmd import task_show_command

    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)
    mgr.update_decision(session.session_id, "task_failure", {
        "task_legacy": {
            "attempt": 2,
            "spec": "the newer spec",
            "original_spec": "the newer spec",
            "superseded_spec": "the spec somebody replaced",
            "failed_validators": [],
            "files_changed": [],
        }
    })
    capsys.readouterr()

    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: project_root)
    with patch("snodo.infrastructure.session.SessionManager", return_value=mgr):
        assert task_show_command(SimpleNamespace(task_id="task_legacy", json=False)) == 0

    assert "the spec somebody replaced" in capsys.readouterr().out


def test_recording_nothing_to_supersede_is_a_no_op(tmp_path, monkeypatch):
    """A retry with no discarded spec writes no recovery record at all."""
    from snodo.cli.commands.run_cmd import _record_superseded_spec

    project_root, mgr, session, executed, audit = _setup(tmp_path, monkeypatch)

    _record_superseded_spec(session, mgr, audit, "task_rate", "", "a replacement")

    entry = mgr.load_session(session.session_id).checkpoint.decisions.get("task_failure", {})
    assert entry == {}
    assert not audit.get_history("spec_replaced")


def test_retry_help_names_the_default():
    """The flag help must say what a bare retry does, before an operator has to
    discover it by reading a failure."""
    import typer
    from typer.main import get_group

    from snodo.cli.commands.run_cmd import register

    run_app = typer.Typer()
    register(run_app)
    command = get_group(run_app).commands["run"]
    params = {p.name: p for p in command.params}
    assert "append_spec" in params and "replace_spec" in params
    assert "keeping its spec" in params["retry"].help
    assert "on top of" in params["append_spec"].help
    assert "replace" in params["replace_spec"].help.lower()
