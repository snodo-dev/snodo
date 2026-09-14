"""Verdict caching against what a judge actually judged (Fixes #246).

A verdict is bought once for a given question.  These tests pin the contract:

- an unchanged spec with unchanged criteria is not re-judged, and the reuse is
  visible (``reused`` on the result and in its canonical record);
- editing one criterion invalidates that validator's entry and no other's;
- changing the model invalidates;
- a tool-using/post-execute judge re-judges when the tree it judged changed,
  even though the spec did not;
- an abstention, an error and a skipped pass are never stored;
- an emptied/corrupt cache produces the same verdicts and outcome as a cold
  run.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult, result_record
from snodo.engine.policy import PolicyAction, PolicyEvaluator
from snodo.validators.registry import _default_registry
from snodo.validators.runner import (
    _is_cacheable_verdict,
    run_validators,
)
from snodo.validators.verdict_cache import (
    VerdictCache,
    cache_for_project,
    default_cache_path,
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _protocol(validators, mode_id="producer", version="1.0.0"):
    return Protocol(
        protocol_id="p_cache",
        name="Verdict Cache Protocol",
        version=version,
        modes=[
            Mode(
                mode_id=mode_id,
                name="Producer",
                tools=["edit"],
                validators=[v.validator_id for v in validators],
            )
        ],
        validators=list(validators),
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode=mode_id,
    )


def _validator(vid="security", criteria=("No secret in code",), **kwargs):
    return Validator(
        validator_id=vid,
        validator_type=kwargs.pop("validator_type", "security"),
        criteria=list(criteria),
        **kwargs,
    )


def _response(content):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


class _Completion:
    """A free-text completion double that records which judge called it."""

    def __init__(self, severity="pass", justification="ok"):
        self.severity = severity
        self.justification = justification
        self.calls = []

    def __call__(self, **kwargs):
        role = (kwargs.get("metadata") or {}).get("role", "")
        self.calls.append(role)
        return _response(
            json.dumps(
                {"severity": self.severity, "justification": self.justification}
            )
        )

    def count(self, role=None):
        if role is None:
            return len(self.calls)
        return sum(1 for call in self.calls if call == role)


class _ToolCompletion:
    """A tool-loop completion double that immediately submits a verdict."""

    def __init__(self, severity="pass", justification="ok"):
        self.severity = severity
        self.justification = justification
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        resp = MagicMock()
        msg = MagicMock()
        msg.content = ""
        tool_call = MagicMock()
        tool_call.function.name = "submit_verdict"
        tool_call.function.arguments = json.dumps(
            {"severity": self.severity, "justification": self.justification}
        )
        msg.tool_calls = [tool_call]
        resp.choices = [MagicMock()]
        resp.choices[0].message = msg
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
        return resp


@pytest.fixture(autouse=True)
def _force_free_text_path():
    """Force the legacy completion path so the LLM double is deterministic."""
    with patch(
        "snodo.validators.llm_validator.supports_response_schema",
        return_value=False,
    ):
        yield


def _run(
    protocol,
    validators,
    cache,
    completion,
    *,
    default_model="test-model",
    phase="pre_execute",
    workspace_mcp=None,
    git_mcp=None,
    task=None,
    audit_log=None,
):
    return run_validators(
        protocol=protocol,
        validators=validators,
        task=task or Task(id="t1", spec="Implement feature X"),
        phase=phase,
        completion_fn=completion,
        default_model=default_model,
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
        workspace_mcp=workspace_mcp,
        git_mcp=git_mcp,
        current_mode=protocol.initial_mode,
        audit_log=audit_log,
        verdict_cache=cache,
    )


def _cache(tmp_path):
    return VerdictCache(
        tmp_path / "verdict_cache.json", project_root=str(tmp_path)
    )


# ---------------------------------------------------------------------------
# The key protects the question
# ---------------------------------------------------------------------------


def test_unchanged_spec_and_criteria_reuse_verdict_and_mark_it(tmp_path):
    validator = _validator()
    protocol = _protocol([validator])
    cache = _cache(tmp_path)
    completion = _Completion()

    first, _ = _run(protocol, [validator], cache, completion)
    second, _ = _run(protocol, [validator], cache, completion)

    assert first[0].severity == "pass"
    assert first[0].reused is False
    assert second[0].severity == "pass"
    assert second[0].reused is True
    assert completion.count() == 1, "the judge was asked the same question twice"
    # The canonical record — and therefore every audit surface built on it —
    # says the judgement was reused.
    assert result_record(second[0])["reused"] is True
    assert "reused" not in result_record(first[0])


def test_editing_one_criterion_invalidates_only_that_validator(tmp_path):
    a = _validator(vid="sec_a", criteria=("criterion a",))
    b = _validator(vid="sec_b", criteria=("criterion b",))
    cache = _cache(tmp_path)
    completion = _Completion()

    _run(_protocol([a, b]), [a, b], cache, completion)
    assert completion.count() == 2

    a_edited = _validator(vid="sec_a", criteria=("criterion a edited",))
    results, _ = _run(
        _protocol([a_edited, b]), [a_edited, b], cache, completion
    )
    by_id = {r.validator_id: r for r in results}

    assert completion.count() == 3, "only the edited criterion's judge re-ran"
    assert by_id["sec_a"].reused is False
    assert by_id["sec_b"].reused is True


def test_changing_the_model_invalidates(tmp_path):
    validator = _validator()
    protocol = _protocol([validator])
    cache = _cache(tmp_path)
    completion = _Completion()

    _run(protocol, [validator], cache, completion, default_model="model-a")
    second, _ = _run(
        protocol, [validator], cache, completion, default_model="model-b"
    )

    assert completion.count() == 2
    assert second[0].reused is False


def test_changing_the_protocol_version_invalidates(tmp_path):
    validator = _validator()
    cache = _cache(tmp_path)
    completion = _Completion()

    _run(_protocol([validator], version="1.0.0"), [validator], cache, completion)
    second, _ = _run(
        _protocol([validator], version="2.0.0"), [validator], cache, completion
    )

    assert completion.count() == 2
    assert second[0].reused is False


def test_tree_subject_is_computed_once_per_validate_pass(tmp_path):
    """The tree is digested once per pass, not once per tree-reading judge."""
    a = Validator(
        validator_id="acc_a",
        validator_type="acceptance",
        evaluation_phase="post_execute",
        criteria=["a"],
        tools=["read_file"],
    )
    b = Validator(
        validator_id="acc_b",
        validator_type="acceptance",
        evaluation_phase="post_execute",
        criteria=["b"],
        tools=["read_file"],
    )
    protocol = _protocol([a, b])
    cache = _cache(tmp_path)
    completion = _ToolCompletion()

    workspace = MagicMock()
    workspace.project_root = "/tmp/project"
    workspace.read_file.return_value = "code"
    git = MagicMock()
    git.get_head_sha.return_value = "sha"
    git.read_diff.return_value = "diff"
    git.get_status.return_value = "clean"

    _run(
        protocol,
        [a, b],
        cache,
        completion,
        phase="post_execute",
        workspace_mcp=workspace,
        git_mcp=git,
    )

    assert git.get_head_sha.call_count == 1
    assert git.read_diff.call_count == 1
    assert git.get_status.call_count == 1


def test_tool_reading_validator_rejudges_when_the_tree_changes(tmp_path):
    validator = Validator(
        validator_id="acceptance",
        validator_type="acceptance",
        evaluation_phase="post_execute",
        criteria=["artifacts complete"],
        tools=["read_file"],
    )
    protocol = _protocol([validator])
    cache = _cache(tmp_path)
    completion = _ToolCompletion()

    workspace = MagicMock()
    workspace.project_root = "/tmp/project"
    workspace.read_file.return_value = "code"
    git = MagicMock()
    git.get_head_sha.side_effect = ["sha1", "sha2", "sha2"]
    git.read_diff.return_value = "diff"
    git.get_status.return_value = "clean"

    def run():
        return _run(
            protocol,
            [validator],
            cache,
            completion,
            phase="post_execute",
            workspace_mcp=workspace,
            git_mcp=git,
        )

    first, _ = run()
    second, _ = run()
    third, _ = run()

    assert first[0].reused is False
    assert second[0].reused is False, "the tree changed, so the judge re-ran"
    assert third[0].reused is True, "the same tree, so the verdict was reused"
    assert completion.calls == 2


@pytest.mark.parametrize("validator_type", ["security", "acceptance"])
def test_post_execute_judge_without_tools_rejudges_on_tree_change(
    tmp_path, validator_type
):
    """A post-execute judge is keyed on the work, never the unchanged spec.

    This is the inheritance trap: ``AcceptanceValidator`` inherits
    ``cache_subject = "spec"`` from ``LLMValidator``, and a post-execute judge
    declared without tools would otherwise reuse attempt one's verdict about
    code attempt two rewrote.  Because the reused prose is byte-identical, the
    recovery loop would then read it as a repeated verdict and halt as
    ``recovery_stalled`` — a task blocked because a judge was never asked.
    """
    validator = Validator(
        validator_id="work_judge",
        validator_type=validator_type,
        evaluation_phase="post_execute",
        criteria=["the produced work is correct"],
    )
    protocol = _protocol([validator])
    cache = _cache(tmp_path)
    completion = _Completion()

    workspace = MagicMock()
    workspace.project_root = "/tmp/project"
    git = MagicMock()
    git.get_head_sha.side_effect = ["sha1", "sha2"]
    git.read_diff.return_value = "diff"
    git.get_status.return_value = "clean"

    def run():
        return _run(
            protocol,
            [validator],
            cache,
            completion,
            phase="post_execute",
            workspace_mcp=workspace,
            git_mcp=git,
        )

    first, _ = run()
    second, _ = run()

    assert first[0].reused is False
    assert second[0].reused is False, "same spec but a different tree must re-judge"
    assert completion.count() == 2


# ---------------------------------------------------------------------------
# What must never be stored
# ---------------------------------------------------------------------------


class _SentinelValidator:
    """Registered judge returning a caller-supplied sentinel result."""

    cache_subject = "spec"
    calls = 0
    _result = None

    @classmethod
    def registered_type(cls):
        return "sentinel_test"

    def __init__(self, validator_spec=None, **kwargs):
        self.validator_spec = validator_spec

    def evaluate(self, context):
        type(self).calls += 1
        return type(self)._result


@pytest.fixture
def sentinel_registry():
    _default_registry.register("sentinel_test", _SentinelValidator)
    yield _SentinelValidator
    _default_registry._registry.pop("sentinel_test", None)
    _SentinelValidator.calls = 0
    _SentinelValidator._result = None


@pytest.mark.parametrize(
    "result",
    [
        ValidatorResult(
            validator_id="sentinel",
            severity="blocker",
            justification="operational fault",
            error=True,
        ),
        ValidatorResult(
            validator_id="sentinel",
            severity="pass",
            justification="gate skipped",
            skipped=True,
        ),
    ],
    ids=["error", "skipped_pass"],
)
def test_non_verdicts_are_never_stored(tmp_path, sentinel_registry, result):
    sentinel_registry._result = result
    validator = _validator(vid="sentinel", validator_type="sentinel_test")
    protocol = _protocol([validator])
    cache = _cache(tmp_path)

    first, _ = _run(protocol, [validator], cache, completion=None)
    second, _ = _run(protocol, [validator], cache, completion=None)

    assert len(cache) == 0, "a non-verdict must not be persisted"
    assert sentinel_registry.calls == 2, "the sentinel was re-judged, not reused"
    assert first[0].reused is False and second[0].reused is False


@pytest.mark.parametrize(
    "result, cacheable",
    [
        (
            ValidatorResult(
                validator_id="v", severity="pass", justification="ok"
            ),
            True,
        ),
        (
            ValidatorResult(
                validator_id="v", severity="warn", justification="hmm"
            ),
            True,
        ),
        (
            ValidatorResult(
                validator_id="v", severity="blocker", justification="no"
            ),
            True,
        ),
        (
            ValidatorResult(
                validator_id="v",
                severity="blocker",
                justification="fault",
                error=True,
            ),
            False,
        ),
        (
            ValidatorResult(
                validator_id="v",
                severity="pass",
                justification="skipped",
                skipped=True,
            ),
            False,
        ),
    ],
)
def test_cacheable_verdict_classification(result, cacheable):
    assert _is_cacheable_verdict(result) is cacheable


# ---------------------------------------------------------------------------
# Cache failures are a miss, never a halt
# ---------------------------------------------------------------------------


def test_corrupt_cache_loads_empty_and_judges_fresh(tmp_path):
    path = tmp_path / "verdict_cache.json"
    path.write_text("{not valid json", encoding="utf-8")

    validator = _validator()
    protocol = _protocol([validator])
    cache = VerdictCache(path, project_root=str(tmp_path))
    assert len(cache) == 0

    completion = _Completion()
    results, _ = _run(protocol, [validator], cache, completion)
    assert results[0].severity == "pass"
    assert results[0].reused is False
    assert completion.count() == 1


def test_unwritable_cache_is_a_debug_line_not_a_halt(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a file", encoding="utf-8")
    cache = VerdictCache(blocker / "verdict_cache.json", project_root=str(tmp_path))

    validator = _validator()
    protocol = _protocol([validator])
    completion = _Completion()
    results, _ = _run(protocol, [validator], cache, completion)

    assert results[0].severity == "pass"
    assert results[0].reused is False
    assert completion.count() == 1


def test_cache_does_not_cross_projects(tmp_path):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    shared = tmp_path / "verdict_cache.json"

    validator = _validator()
    protocol = _protocol([validator])
    completion = _Completion()

    _run(protocol, [validator], VerdictCache(shared, project_root=str(project_a)), completion)
    second, _ = _run(
        protocol,
        [validator],
        VerdictCache(shared, project_root=str(project_b)),
        completion,
    )

    assert completion.count() == 2
    assert second[0].reused is False


# ---------------------------------------------------------------------------
# Cold cache == today
# ---------------------------------------------------------------------------


def test_emptied_cache_produces_same_verdicts_and_outcome(tmp_path):
    warn = _validator(vid="meta", criteria=("spec is clear",))
    blocker = _validator(vid="security", criteria=("no secrets",))
    protocol = _protocol([warn, blocker])

    def completion(**kwargs):
        role = (kwargs.get("metadata") or {}).get("role", "")
        severity = "warn" if "meta" in role else "blocker"
        return _response(
            json.dumps(
                {"severity": severity, "justification": f"{role}:{severity}"}
            )
        )

    # Cold: no cache at all.
    cold_results, cold_caps = _run(protocol, [warn, blocker], None, completion)

    # A real cache, then the same run with it emptied.
    cache = _cache(tmp_path)
    warm_results, warm_caps = _run(protocol, [warn, blocker], cache, completion)
    cache.clear()
    emptied_results, emptied_caps = _run(
        protocol, [warn, blocker], cache, completion
    )

    def shape(results):
        return [(r.validator_id, r.severity) for r in results]

    assert shape(cold_results) == shape(warm_results) == shape(emptied_results)
    assert cold_caps == warm_caps == emptied_caps

    policy = PolicyEvaluator()
    cold_decision = policy.evaluate(
        cold_results, protocol.disagreement_policy, "pre_execute"
    )
    empty_decision = policy.evaluate(
        emptied_results, protocol.disagreement_policy, "pre_execute"
    )
    assert cold_decision.action == empty_decision.action


def test_reused_verdict_is_still_the_verdict_for_the_quorum(tmp_path):
    validator = _validator()
    protocol = _protocol([validator])
    cache = _cache(tmp_path)
    completion = _Completion()

    _run(protocol, [validator], cache, completion)
    reused, _ = _run(protocol, [validator], cache, completion)

    assert reused[0].severity == "pass"
    decision = PolicyEvaluator().evaluate(
        reused, protocol.disagreement_policy, "pre_execute"
    )
    assert decision.action == PolicyAction.PROCEED


# ---------------------------------------------------------------------------
# Storage hygiene
# ---------------------------------------------------------------------------


def test_cache_for_project_lives_under_the_project_snodo(tmp_path):
    cache = cache_for_project(tmp_path)

    assert cache is not None
    assert cache.path == default_cache_path(tmp_path)
    assert cache.path.parent.name == ".snodo"


def test_clear_deletes_the_file_and_is_idempotent(tmp_path):
    cache = _cache(tmp_path)
    cache.put(
        "k",
        ValidatorResult(validator_id="v", severity="pass", justification="ok"),
    )
    assert cache.path.exists()

    cache.clear()
    assert not cache.path.exists()
    assert len(cache) == 0

    cache.clear()  # a second clear must not raise


def test_cache_evicts_least_recently_used(tmp_path):
    cache = VerdictCache(
        tmp_path / "c.json", project_root=str(tmp_path), max_entries=2
    )
    for key in ("a", "b", "c"):
        cache.put(
            key,
            ValidatorResult(
                validator_id=key, severity="pass", justification="ok"
            ),
        )

    assert len(cache) == 2
    assert cache.get("a") is None
    assert cache.get("c") is not None


def test_cache_ignores_schema_mismatch(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "schema": 999,
                "project": str(tmp_path),
                "entries": {"k": {"severity": "pass"}},
            }
        ),
        encoding="utf-8",
    )

    assert len(VerdictCache(path, project_root=str(tmp_path))) == 0


def test_cache_ignores_a_foreign_project(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "project": "/some/other/project",
                "entries": {"k": {"severity": "pass"}},
            }
        ),
        encoding="utf-8",
    )

    assert len(VerdictCache(path, project_root=str(tmp_path))) == 0


def test_cache_drops_malformed_entries(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "project": str(tmp_path),
                "entries": {"good": {"severity": "pass"}, "bad": "not a dict"},
            }
        ),
        encoding="utf-8",
    )
    cache = VerdictCache(path, project_root=str(tmp_path))

    assert len(cache) == 1
    assert cache.get("good") is not None
    assert cache.get("bad") is None
