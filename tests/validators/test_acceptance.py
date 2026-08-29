"""Tests for the acceptance validator (Fixes #54).

The acceptance validator judges the produced artifacts against the task's
acceptance criteria.  It must:
- distinguish "unmet" (verifiable from the tree, demonstrably absent) from
  "uncheckable" (device behaviour, human judgement — never a finding);
- warn, not block, on a miss (severity_cap: warn in the shipped templates);
- not become a second `quality` — it judges completeness against the spec,
  never correctness of the code.
"""

import json
from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.acceptance import AcceptanceValidator
from snodo.validators.context import ValidatorContext
from snodo.validators.registry import _default_registry


def _make_response(content=None, tool_calls=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls or []
    resp.choices[0].finish_reason = "tool_calls"
    return resp


def _verdict_call(severity, justification):
    tc = MagicMock()
    tc.id = "call_verdict"
    tc.function.name = "submit_verdict"
    tc.function.arguments = json.dumps({
        "severity": severity,
        "justification": justification,
    })
    return tc


def _read_call(path="src/main.py"):
    tc = MagicMock()
    tc.id = "call_read"
    tc.function.name = "read_file"
    tc.function.arguments = json.dumps({"path": path})
    return tc


def _validator():
    return Validator(
        validator_id="acceptance",
        validator_type="acceptance",
        evaluation_phase="post_execute",
        severity_cap="warn",
        tools=["read_file", "list_files", "read_diff_between_refs"],
        criteria=["Judge the produced artifacts against the acceptance criteria"],
    )


def _context(completion_fn, artifacts=None, workspace=None, git=None):
    return ValidatorContext(
        task=Task(id="t1", spec=(
            "Add a login endpoint.\n"
            "Acceptance criteria:\n"
            "1. A test covers the new endpoint.\n"
            "2. The endpoint rejects invalid tokens.\n"
            "3. The feature is documented in docs/decisions/."
        )),
        completion_fn=completion_fn,
        workspace_mcp=workspace or MagicMock(),
        git_mcp=git or MagicMock(),
        phase="post_execute",
        max_tool_turns=5,
        artifacts=artifacts or [],
    )


class TestAcceptanceValidatorRegistered:
    def test_registered_type(self):
        assert AcceptanceValidator.registered_type() == "acceptance"

    def test_registry_contains_acceptance(self):
        assert _default_registry.lookup("acceptance") is AcceptanceValidator


class TestAcceptanceValidatorPrompt:
    def test_prompt_contains_artifacts_and_acceptance_instructions(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "all met")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=["src/main.py", "tests/test_main.py"])

        validator.evaluate(ctx)

        prompt = completion_fn.call_args[1]["messages"][0]["content"]
        assert "src/main.py" in prompt
        assert "tests/test_main.py" in prompt
        assert "Acceptance criteria" in prompt
        assert "UNMET" in prompt
        assert "UNCHECKABLE" in prompt
        assert "NEVER a finding" in prompt

    def test_prompt_notes_when_no_artifacts(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "no criteria")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=[])

        validator.evaluate(ctx)

        prompt = completion_fn.call_args[1]["messages"][0]["content"]
        assert "(none)" in prompt


class TestAcceptanceValidatorVerdicts:
    def test_pass_when_all_met(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "all acceptance criteria met")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        assert result.severity == "pass"

    def test_warn_when_criterion_unmet(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("warn", "criterion 1 unmet: no test covers the endpoint")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        assert result.severity == "warn"

    def test_uncheckable_criterion_is_not_a_finding(self):
        """A criterion that cannot be verified from the tree must not block."""
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "criterion 3 is uncheckable from the tree; others met")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        assert result.severity == "pass"

    def test_tool_loop_reads_files_before_verdict(self):
        workspace = MagicMock()
        workspace.read_file.return_value = "def login(): pass"
        completion_fn = MagicMock(side_effect=[
            _make_response(tool_calls=[_read_call()]),
            _make_response(tool_calls=[_verdict_call("pass", "all met")]),
        ])
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(
            completion_fn, artifacts=["src/main.py"], workspace=workspace,
        ))

        assert result.severity == "pass"
        workspace.read_file.assert_called_once_with("src/main.py")

    def test_severity_cap_keeps_miss_at_warn(self):
        """Even if the judge returns blocker, the shipped severity_cap=warn
        keeps a miss recoverable rather than a hard halt."""
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("blocker", "criterion 1 unmet")],
        ))
        validator = AcceptanceValidator(_validator())
        result = validator.evaluate(_context(completion_fn, artifacts=["src/main.py"]))

        # The validator itself reports what the judge said; the cap is applied
        # by the shared runner (run_validators), which is what the engine uses.
        assert result.severity == "blocker"

    def test_runner_caps_acceptance_blocker_to_warn(self):
        """The shared runner applies severity_cap=warn to the acceptance
        validator, so a miss routes to recovery, not a hard halt."""
        from snodo.validators.runner import run_validators

        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("blocker", "criterion 1 unmet")],
        ))
        protocol = MagicMock()
        protocol.get_mode.return_value = MagicMock(name="producer", tools=[], transitions={}, validators=[])
        protocol.get_validator.return_value = _validator()

        results, _ = run_validators(
            protocol=protocol,
            validators=[_validator()],
            task=Task(id="t1", spec="Add a login endpoint."),
            phase="post_execute",
            completion_fn=completion_fn,
            default_model="gpt-4",
            validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
            workspace_mcp=MagicMock(),
            git_mcp=MagicMock(),
            current_mode="producer",
            artifacts=["src/main.py"],
        )

        assert results[0].severity == "warn"

    def test_no_acceptance_criteria_returns_pass(self):
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "no acceptance criteria in spec")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=["src/main.py"])
        ctx.task = Task(id="t1", spec="Add a login endpoint.")

        result = validator.evaluate(ctx)

        assert result.severity == "pass"


class TestAcceptanceValidatorNotQuality:
    def test_prompt_never_mentions_running_tests(self):
        """It judges completeness against the spec, not correctness of code."""
        completion_fn = MagicMock(return_value=_make_response(
            tool_calls=[_verdict_call("pass", "all met")],
        ))
        validator = AcceptanceValidator(_validator())
        ctx = _context(completion_fn, artifacts=["src/main.py"])

        validator.evaluate(ctx)

        prompt = completion_fn.call_args[1]["messages"][0]["content"]
        assert "test command" not in prompt.lower()
        assert "pytest" not in prompt.lower()
        assert "npm test" not in prompt.lower()


class TestArtifactsThreadedToPostValidate:
    """The produced artifacts reach the validator context at post-execute."""

    def test_post_validate_passes_artifacts_to_validator(self):
        from snodo.compiler.models import Mode, Protocol
        from snodo.engine.loop import GraphBuilder

        protocol = Protocol(
            protocol_id="p",
            name="P",
            version="1.0.0",
            initial_mode="producer",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                        validators=["acceptance"])],
            validators=[_validator()],
        )

        seen = {}

        def tracking_validator(task, validators, shell_mcp, current_mode="", **kwargs):
            seen["artifacts"] = kwargs.get("artifacts")
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass", justification="stub")
                for v in validators
            ]

        builder = GraphBuilder(protocol, validator_fn=tracking_validator)

        state = {
            "task": {"id": "t1", "spec": "Add a login endpoint."},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "execute",
            "validation_results": [],
            "validation_token": None,
            "artifacts": ["src/main.py", "tests/test_main.py"],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
        }

        builder._post_validate_node(state)

        assert seen["artifacts"] == ["src/main.py", "tests/test_main.py"]


# ---------------------------------------------------------------------------
# Deterministic canary (Fixes #59): a real judge notices a real omission.
#
# The standing finding is that every read-only judge passes everything it is
# shown.  The tests above assert the prompt produces a verdict; they do NOT
# establish that a judge driven by the actual tree rejects a real omission.
# These canaries run the validator's real tool loop against a real repository
# (a temp directory) with a deterministic judge whose verdict is *caused by*
# what the tree actually contains: it lists the tree, reads the file a
# verifiable criterion demands, and emits warn only when that evidence is
# absent.  If a future change severs the loop (criteria unreachable, tools
# unhelpful, verdict decoupled from evidence) the canary fails.
# ---------------------------------------------------------------------------

class _JudgeWorkspace:
    """Minimal workspace stub over a real dict of path → content."""

    def __init__(self, tree):
        self.tree = dict(tree)
        self.reads = []

    def list_files(self, directory="."):
        prefix = directory.rstrip("/") + "/" if directory not in ("", ".") else ""
        return sorted(
            p for p in self.tree if p.startswith(prefix)
        ) or []

    def read_file(self, path):
        self.reads.append(path)
        if path not in self.tree:
            raise FileNotFoundError(f"File not found: {path}")
        return self.tree[path]

    def read_file_lines(self, path, start, end):
        return self.read_file(path)


class _CriteriaAwareJudge:
    """Deterministic judge that inspects the tree via the tool loop, then
    reasons from the criteria.

    Plan of probes: list the tree, then ``read_file`` each distinct evidence
    path that the criteria demand (one probe per turn).  The verdict is
    computed from the tool loop's actual results: a probe whose tool message
    reports "File not found" means the tree lacks that evidence path, and a
    criterion whose evidence path is missing → warn naming the criterion.  A
    criterion with no tree-verifiable evidence path (device behaviour, human
    judgement) is uncheckable and never a finding.  If the tool loop ever
    fails to deliver the probes, the verdict falls back to pass-with-no-evidence,
    which the canaries would not assert as a warn — so a severed loop fails
    the tests.
    """

    def __init__(self):
        self.turns = 0
        # path -> tool-loop result ("File not found: X" or the file content)
        self.probes = {}
        self._pending_path = None

    def __call__(self, **kwargs):
        self.turns += 1
        messages = kwargs.get("messages", [])
        tools = kwargs.get("tools", [])
        tool_names = {
            t.get("function", {}).get("name")
            for t in tools if isinstance(t, dict)
        }
        prompt = messages[0]["content"]
        last_tool = None
        for m in reversed(messages):
            if m.get("role") == "tool":
                last_tool = m.get("content", "")
                break

        if self.turns == 1 and "list_files" in tool_names:
            return _make_response(tool_calls=[
                _pick_tool_call("list_files", "probe_list", {"directory": "."})])

        if "read_file" in tool_names:
            # The previous read probe's result arrives in this turn's messages.
            if self._pending_path is not None and last_tool is not None:
                self.probes[self._pending_path] = last_tool
            path = self._next_probe_path(prompt)
            if path is not None:
                self._pending_path = path
                return _make_response(tool_calls=[
                    _pick_tool_call("read_file", "probe_read", {"path": path})])

        severity, justification = self._verdict(prompt)
        return _make_response(tool_calls=[_verdict_call(severity, justification)])

    def _next_probe_path(self, prompt):
        """Return the next evidence path to probe, or None when all done."""
        for c in _make_criteria_from_prompt(prompt):
            for p in _evidence_paths(c):
                if p not in self.probes:
                    return p
        return None

    def _verdict(self, prompt):
        criteria = _make_criteria_from_prompt(prompt)
        unmet = []
        for c in criteria:
            paths = _evidence_paths(c)
            if not paths:
                # No tree-verifiable evidence path — uncheckable (device
                # behaviour, human judgement). Never a finding.
                continue
            if not any(self._probe_found(p) for p in paths):
                unmet.append(c)
        if unmet:
            return "warn", (
                f"Acceptance criterion unmet: {unmet[0]} — the tree lacks "
                f"the evidence it requires ({_evidence_paths(unmet[0])[0]})."
            )
        return "pass", "all verifiable acceptance criteria are met"

    def _probe_found(self, path):
        """True when the tool loop's probe for *path* succeeded.

        The probe result is the loop's own report: a "File not found" tool
        error is how the loop tells the judge the file is absent.  The verdict
        is therefore caused by the actual tool loop output, not by the judge's
        private copy of the tree.

        A path that was never probed is treated as *not a finding*: the judge
        cannot fault a criterion it gathered no evidence on.  This is what
        makes the canary fail when the loop is severed — a judge that never
        received the probes cannot produce the warn the canary asserts.
        """
        result = self.probes.get(path)
        if result is None:
            return True  # unknown, not a finding
        return "File not found" not in result


def _make_criteria_from_prompt(prompt):
    """Extract the acceptance criteria lines from the judge prompt.

    The criteria live inside the ``## Task`` section; the word "acceptance
    criteria" also appears in the judge instructions, so extraction is scoped
    to the task section to avoid mis-parsing the instructions.
    """
    task_section = ""
    sections = prompt.split("\n## ")
    for section in sections:
        if section.startswith("Task\n"):
            task_section = section[len("Task\n"):]
            break
    if task_section is None:
        # Fall back to the whole prompt if no Task header is found.
        task_section = prompt

    lines = []
    capturing = False
    for line in task_section.splitlines():
        stripped = line.strip()
        if "acceptance criteria" in stripped.lower() or "done when" in stripped.lower():
            capturing = True
            continue
        if capturing:
            if not stripped:
                break
            lines.append(stripped)
    return lines


def _evidence_paths(criterion):
    """Return the tree paths that would evidence *criterion*, if verifiable.

    A criterion that names no file-like evidence (device behaviour, human
    judgement) is uncheckable: returns an empty list.  This is the same
    distinction the judge prompt draws (MET / UNMET / UNCHECKABLE).

    Evidence is per-criterion, not cumulative: a criterion about a *test*
    requires the test file (the source existing does not satisfy "a test
    exists"); a criterion about the *endpoint* requires the source; a
    criterion about *documentation/decisions* requires the record file.
    """
    lowered = criterion.lower()
    uncheckable_markers = (
        "feel", "judgement", "human", "device", "performance under real load",
        "user experience", "looks", "feels",
    )
    if any(m in lowered for m in uncheckable_markers):
        return []
    if "test" in lowered:
        return ["tests/test_main.py"]
    if "doc" in lowered or "adr" in lowered or "decision" in lowered:
        return ["docs/decisions/0001-record.md"]
    if "endpoint" in lowered or "api" in lowered or "login" in lowered:
        return ["src/main.py"]
    return []



def _make_tool_call(name, call_id, arguments):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


def _pick_tool_call(name, call_id, arguments):
    return _make_tool_call(name, call_id, arguments)


# ---------------------------------------------------------------------------
# The two deterministic canaries (Fixes #59)
# ---------------------------------------------------------------------------

_SPEC_WITH_CRITERIA = (
    "Add a login endpoint.\n"
    "Acceptance criteria:\n"
    "1. A test covers the new endpoint.\n"
    "2. The feature is documented in docs/decisions/.\n"
    "3. The login flow feels natural to a human user.\n"
)

_SPEC_SOURCE_ONLY = (
    "Add a login endpoint.\n"
    "Acceptance criteria:\n"
    "1. A test covers the new endpoint.\n"
    "2. The feature is documented in docs/decisions/.\n"
)

_SPEC_FULL_TREE = (
    "Add a login endpoint.\n"
    "Acceptance criteria:\n"
    "1. A test covers the new endpoint.\n"
    "2. The feature is documented in docs/decisions/.\n"
)


class TestAcceptanceCanary:
    """A deterministic judge driven by the real tree rejects a real omission,
    and passes an uncheckable criterion — the safe direction proven, not
    assumed."""

    def _run(self, spec, tree):
        workspace = _JudgeWorkspace(tree)
        judge = _CriteriaAwareJudge()
        validator = AcceptanceValidator(_validator())
        ctx = _context(judge, artifacts=sorted(tree), workspace=workspace)
        ctx.task = Task(id="t1", spec=spec)
        result = validator.evaluate(ctx)
        return result, judge, workspace

    def test_warns_and_names_criterion_when_test_missing(self):
        """Source exists but the demanded test file does not: warn, naming the
        unmet criterion."""
        tree = {"src/main.py": "def login(): pass"}
        result, judge, workspace = self._run(_SPEC_SOURCE_ONLY, tree)

        assert result.severity == "warn"
        assert "criterion" in result.justification.lower()
        assert "test" in result.justification.lower()
        # The judge probed the tree (the demanded test file) before judging.
        assert "tests/test_main.py" in workspace.reads

    def test_warns_and_names_criterion_when_adr_missing(self):
        # Test exists but the demanded decision record does not: warn on the
        # documented-in-decisions criterion.
        tree = {
            "src/main.py": "def login(): pass",
            "tests/test_main.py": "def test_login(): pass",
        }
        result, judge, workspace = self._run(_SPEC_FULL_TREE, tree)

        assert result.severity == "warn"
        assert "decision" in result.justification.lower()
        assert "docs/decisions/0001-record.md" in result.justification

    def test_passes_when_all_verifiable_criteria_met(self):
        tree = {
            "src/main.py": "def login(): pass",
            "tests/test_main.py": "def test_login(): pass",
            "docs/decisions/0001-record.md": "# Record\nDecision: X\n",
        }
        result, judge, workspace = self._run(_SPEC_FULL_TREE, tree)

        assert result.severity == "pass"

    def test_uncheckable_criterion_passes(self):
        """A criterion that cannot be verified from the tree (device
        behaviour, human judgement) is never a finding — even when the
        verifiable ones are unmet, the uncheckable one must not add a warn."""
        tree = {"src/main.py": "def login(): pass"}
        result, judge, workspace = self._run(_SPEC_WITH_CRITERIA, tree)

        # The uncheckable criterion (3) is not named as unmet; it must never
        # be reported as a miss.
        assert "feel" not in result.justification.lower()
        assert "human" not in result.justification.lower()

    def test_uncheckable_criterion_alone_passes(self):
        """A spec whose only acceptance criterion is uncheckable passes —
        the safe direction proven, not assumed."""
        spec = (
            "Add a login endpoint.\n"
            "Acceptance criteria:\n"
            "1. The login flow feels natural to a human user.\n"
        )
        tree = {"src/main.py": "def login(): pass"}
        result, judge, workspace = self._run(spec, tree)

        assert result.severity == "pass"
        assert "feel" not in result.justification.lower()
