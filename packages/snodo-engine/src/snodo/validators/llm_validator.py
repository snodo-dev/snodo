"""LLM-driven protocol validation with bounded, read-only inspection.

Uses the existing Coder adapter's LLM to evaluate tasks against
protocol-defined criteria before execution.
Judge prompt contract:
- Input: task spec + validator criteria from protocol YAML
- Output: JSON with {severity, justification}
- Falls back to "warn" on any LLM or parse failure
Tool-loop (capability-grant):
- Runs iff validator_spec.tools is non-empty AND MCPs + completion_fn present.
- Empty/absent tools => single-completion path (no loop, no tools).
- Explicit grant only — never defaults to the full set.
- Phase filters read_diff_between_refs (meaningful post-execute only) AND
  reaches the prompt: the judge is told whether it is reviewing a proposal
  (pre-execute) or inspecting a finished result (post-execute), so a
  tool-enabled pre-execute judge cannot read "evaluate the task" as "check
  whether this was done" (see ADR 019).
- The produced change is context, not a capability (Fixes #267): every
  post-execute judge — tool-loop or single-completion, granted the diff tool
  or not — begins its turn with the change already in the prompt
  (snodo.validators.change).  Pre-execute judges never receive it.
- A turn whose tool calls are all repeat-read hits is not charged against the
  turn budget; a small, separate streak counter still stops a judge that only
  ever repeats itself, sooner than the real budget (Fixes #360, ADR 050).

Tool grants and phase framing determine the judge's view. Every live
submit_verdict must name the exact task under judgement (ADR 059); a missing
or different subject is an operational error, not a finding or a warn.
Single-completion JSON judges retain their existing contract.

The tool loop and the legacy single-completion path deliberately have different
response contracts. Tool-loop prose is never a verdict: the final submission
is the boundary at which subject identity is checked, before severity or
capability reports are interpreted. Operational errors remain errors through
criteria enrichment and the runner, rather than becoming findings about work
that a coder or spec author could repair.
"""

import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Set

from snodo.engine.progress import (
    ensure_progress_sink,
    format_elapsed,
    format_tool_call_summary,
)

import litellm as _litellm
from litellm import supports_response_schema
from snodo.coders.tool_result_text import provider_tool_result_text

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.change import change_block_for, ensure_change_context
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.plugin_tool_support import (
    configured_tools, offer_definitions, execute_plugin_tool, is_tool_argument_error,
    tool_access_instruction,
    execute_builtin_tool,
    workspace_root,
)
from snodo.validators.registry import _default_registry
from snodo.validators.verdict_subject import reject_wrong_subject, subject_instruction
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.coders.litellm import ReadMemoryTracker, _normalize_path_arg, format_repeat_read_response
from snodo.validators.llm_provider_errors import (
    call_with_unforced_tool_fallback,
    _is_gemini3_plus,
    _is_provider_rejection,
    _is_transient_error,
    _remove_rejected_parameter,
    _reshape_rejected_messages,
    apply_remembered_message_shape,
    apply_remembered_parameters,
    _provider_retry_delay,
    _usage_tokens,
)

_litellm.drop_params = True
_logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_TURNS = 20
_DEFAULT_MAX_TOKENS = 1500
# A turn whose only tool calls were repeat-read hits made no progress and is
# not charged against max_tool_turns (see _MAX_STALL_TURNS below and ADR 050)
# — but a judge that only ever repeats itself must still be stopped, sooner
# than the real budget rather than looping through it. This bounds how many
# *consecutive* stalled turns are tolerated before the loop forces the same
# verdict-only turn already used at the true end of budget.
# Repeat reads are free, but a bounded consecutive stall forces a verdict.
_MAX_STALL_TURNS = 3
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_BUDGET_SECONDS = 30.0
_RETRY_JITTER_RATIO = 0.5

_READ_ONLY_TOOL_NAMES: Set[str] = {
    "read_file", "read_file_lines", "list_files", "git_show", "git_log",
    "read_diff_between_refs", "summarize_directory",
}
_POST_EXECUTE_ONLY_TOOLS: Set[str] = {"read_diff_between_refs"}

# The instruction that closes the reading window. The nudge after a prose
# answer and the final turn are the same moment — the system has decided the
# reading is over — so they say the same thing: the read tools are withdrawn,
# decide now, and a verdict on a partial view is real ("warn" exists for it).
_VERDICT_ONLY_INSTRUCTION = (
    "The read tools are no longer available. Return your verdict now by "
    "calling submit_verdict(task_id, severity, justification); do not narrate. A "
    "verdict reached on partial reading is a real verdict — if your view is "
    "partial, say so in the justification and use \"warn\"."
)
_UNFORCED_VERDICT_INSTRUCTION = (
    "The provider could not enforce the required tool choice, so you must "
    "follow this instruction from the prompt: call submit_verdict(task_id, severity, "
    "justification) now. Do not answer with prose or remain silent; this is "
    "the final opportunity to deliver a verdict."
)


def _phase_frame(phase: str) -> str:
    """Distinguish a proposal from completed work (ADR 019).

    The same criteria list reads differently depending on phase: at
    pre-execute the judge is reviewing a proposal (the described work does not
    exist yet, and its absence is never a finding); at post-execute it is
    inspecting a finished result (absence of the described work *is* a
    finding).  Without this frame, a tool-enabled pre-execute judge reads
    "evaluate the task against the criteria" as "check whether this was done"
    and blocks on work that cannot exist yet (see ADR 019).

    Before execution, missing implementation is expected, not evidence of a
    violation. After execution the same absence may be a legitimate finding.
    Keep that distinction explicit even when both phases offer read tools.
    """
    if phase == "post_execute":
        return (
            "You are inspecting COMPLETED work. The described change has been "
            "implemented; judge the finished result against the criteria below. "
            "Absence of the described work, or of the tests and tooling it "
            "requires, IS a finding."
        )
    if phase == "mode_transition":
        return (
            "You are reviewing a mode transition. Judge whether the transition "
            "as described satisfies the criteria below."
        )
    # pre_execute (and any unknown phase) — the safe default is the proposal
    # frame: absence of implementation is expected and never a finding.
    return (
        "You are reviewing a PROPOSAL, before any of it has been built. The "
        "repository will NOT contain the described work; that is expected and "
        "is never a finding. Absence of implementation, tooling, tests, or a "
        "passing build is out of scope for this review and must never be "
        "cited. Judge only this: if the proposal were carried out as "
        "described, would it violate a criterion below?"
    )


class LLMValidator(ValidatorBase):
    """Evaluates tasks against protocol criteria using an LLM judge.

    Declared tools select bounded repository inspection when infrastructure
    is available. Judges without tools retain single-completion validation.
    Subclasses can specialize the prompt without replacing the live verdict
    boundary shared by every submit_verdict judge.
    """

    #: A single-completion judge of the task spec.  This is the pre-execute
    #: answer; the runner forces every post-execute judge (including a
    #: subclass that leaves this inherited) onto the tree subject (#246).
    # The runner forces tool-using and all post-execute judges onto the tree.
    cache_subject = "spec"
    VALID_SEVERITIES = {"pass", "warn", "blocker"}
    HANDLED_TYPES = {
        "architecture", "security", "conventions",
        "performance", "testing", "planning",
    }

    def __init__(self, validator_spec: Validator, completion_fn=None, model: str = DEFAULT_MODEL):
        self.validator_spec = validator_spec
        self._completion_fn = completion_fn
        self.model = model
        self.completion_tokens = _DEFAULT_MAX_TOKENS
        self._job_id: str = ""
        self._task_id: str = ""
        self._depth: int = 0
        self._attempt: int = 1
        self._parameter_fallback_used = False

    def _finalize_result(self, result: ValidatorResult) -> ValidatorResult:
        """Prevent caching a verdict produced after changing judge parameters."""
        if self._parameter_fallback_used:
            result.cacheable = False
        return result

    def _emit_turn_telemetry(
        self, turn_index: int, tool: str, target_path: str, read_hit: bool,
        tokens_in: int, tokens_out: int, elapsed_ms: float,
        model_requested: str = "", response: Any = None,
    ) -> None:
        """Best-effort operational telemetry, not part of the audit chain.

        Operational telemetry, not part of the audit chain (ADR 034). Never
        raises — telemetry must not crash the tool loop.
        ``model`` is the name sent in the request; ``served_model`` is the
        provider's own name for what answered, beside it as provenance
        (Fixes #381). None served means the provider reported nothing — not
        a match, and not a substitution either.

        Observability failures must not change a judge's verdict. The runner
        records the canonical result independently of this per-turn detail.
        """
        try:
            from snodo.infrastructure.tool_telemetry import (
                canonical_target_path, persist_tool_telemetry,
            )
            from snodo.infrastructure.model_provenance import served_model_of

            record = {
                "task_ref": self._task_id or "unknown",
                "depth": getattr(self, "_depth", 0) or 0,
                "attempt": getattr(self, "_attempt", 1) or 1,
                "role": "validator",
                "validator_id": self.validator_spec.validator_id,
                "turn_index": turn_index,
                "tool": tool,
                "target_path": canonical_target_path(target_path),
                "read_hit": bool(read_hit),
                "model": model_requested,
                "served_model": served_model_of(response),
                "tokens_in": int(tokens_in or 0),
                "tokens_out": int(tokens_out or 0),
                "elapsed_ms": round(float(elapsed_ms or 0), 1),
                "submit_bytes": 0,
            }
            persist_tool_telemetry(self._job_id or "unknown", record)
        except Exception as e:
            _logger.warning("Failed to persist tool telemetry: %s", e)

    @classmethod
    def registered_type(cls) -> str:
        return "llm"

    def evaluate(self, context_or_task) -> ValidatorResult:
        """Evaluate a task or the runner's richer phase-aware context.

        Keep the Task entry point for direct callers. Runner context supplies
        tools, budgets and attribution, but the task itself remains the source
        of truth for the subject being judged, not telemetry's task_id.
        """
        if isinstance(context_or_task, Task):
            context = ValidatorContext(
                task=context_or_task, completion_fn=self._completion_fn, model=self.model,
            )
        else:
            context = context_or_task
            if context.completion_fn is not None:
                self._completion_fn = context.completion_fn
            if context.model:
                self.model = context.model
            ctx_tokens = getattr(context, "max_tokens", None)
            if ctx_tokens is not None:
                self.completion_tokens = ctx_tokens
            self._job_id = getattr(context, "job_id", "") or ""
            self._task_id = getattr(context, "task_id", "") or ""
            self._depth = getattr(context.task, "depth", 0) or 0
            self._attempt = (getattr(context.task, "depth", 0) or 0) + 1

        # Capability gate: tool-loop runs iff validator declares tools
        # AND MCPs + completion_fn are present. Empty/absent tools =>
        # single-completion path (no loop, no tools). Explicit grant only.
        from snodo.validators.runner import enrich_result_with_criteria

        declared_tools = getattr(self.validator_spec, "tools", None) or []
        plugin_config = configured_tools(self.validator_spec)
        if declared_tools or plugin_config:
            workspace_tools = {"read_file", "read_file_lines", "list_files", "summarize_directory"}
            git_tools = {"read_diff_between_refs", "git_show", "git_log"}
            missing_dependencies = []
            if workspace_tools.intersection(declared_tools) and context.workspace_mcp is None:
                missing_dependencies.append("workspace MCP")
            if git_tools.intersection(declared_tools) and context.git_mcp is None:
                missing_dependencies.append("git MCP")
            if self._completion_fn is None:
                missing_dependencies.append("completion function")
            if missing_dependencies:
                # Pre-execute validation may run before the execution context
                # (workspace/git MCPs) exists. Preserve its ordinary
                # single-completion behavior; post-execute judges must have
                # their declared inspection tools or report infrastructure
                # failure rather than inventing a verdict.
                # Preserve pre-execute single-completion fallback; a post-execute
                # judge must have its declared inspection infrastructure.
                if getattr(context, "phase", "") == "post_execute":
                    return ValidatorResult(
                        validator_id=self.validator_spec.validator_id,
                        severity="blocker",
                        justification=(
                            "Validator tool infrastructure unavailable: missing "
                            + ", ".join(missing_dependencies)
                        ),
                        error=True,
                    )
            else:
                res = self._evaluate_with_tools(context)
                return enrich_result_with_criteria(self._finalize_result(res), getattr(self.validator_spec, "criteria", []))

        prompt = self._build_prompt(context)
        # Try structured output when the model supports it.  Structured output
        # must DEGRADE, not fail: a provider that rejects response_format (e.g.
        # DeepSeek's "This response_format type is unavailable now") must not
        # take the validator down — fall back to an unstructured call and parse
        # the verdict from the content (Fixes #84).
        structured_rejected = False
        if self._completion_fn is not None and supports_response_schema(self.model):
            try:
                res = self._call_llm_structured(prompt)
                return enrich_result_with_criteria(self._finalize_result(res), getattr(self.validator_spec, "criteria", []))
            except Exception as e:
                structured_rejected = _is_provider_rejection(e)

        if self._completion_fn is None:
            res = ValidatorResult(
                validator_id=self.validator_spec.validator_id, severity="blocker",
                justification="No completion_fn available", error=True,
            )
            return enrich_result_with_criteria(self._finalize_result(res), getattr(self.validator_spec, "criteria", []))
        try:
            response_text = self._call_llm(prompt)
            res = self._parse_response(response_text)
            # If the provider rejected structured output AND the unstructured
            # fallback did not parse, that is an operational fault, not a
            # verdict — the provider refused the structured call and the
            # fallback yielded nothing usable (Fixes #84).
            # A provider refusing structured output must not turn an unusable
            # fallback response into a warn that could participate in consensus.
            if structured_rejected and res.severity == "warn" \
                    and "Could not parse" in res.justification:
                res = ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="blocker",
                    justification=(
                        "Structured output was rejected by the provider and the "
                        f"unstructured fallback did not parse: {res.justification}"
                    ),
                    error=True,
                )
        except Exception as e:
            # The operational-fault path: an error=True result halts the run
            # as validator_error, so the cause must travel with it. The type
            # distinguishes provider rejections from code defects, and the
            # message carries the provider's own words.
            _logger.warning(
                "Validator %s (model=%s) hit an operational fault: %s: %s",
                self.validator_spec.validator_id, self.model, type(e).__name__, e,
            )
            res = ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="blocker",
                justification=(
                    f"LLM validation failed due to operational error "
                    f"({type(e).__name__}): {e}"
                ),
                error=True,
            )
        return enrich_result_with_criteria(self._finalize_result(res), getattr(self.validator_spec, "criteria", []))

    # ------------------------------------------------------------------
    # Post-execute bounded tool-use loop
    # ------------------------------------------------------------------
    def _evaluate_with_tools(self, context: ValidatorContext) -> ValidatorResult:
        """Run the bounded read-only loop in either evaluation phase.

        Run a bounded read-only tool-use loop.
        Activated by declared tools on the validator spec (not phase).
        Phase filters read_diff_between_refs (meaningful post-execute only)
        and reaches the prompt via ``_phase_frame``, so the judge knows
        whether it is reviewing a proposal or inspecting a result.

        Only declared tools are offered, and post-execute diff inspection is
        withheld before execution. Reading ends with a verdict-only request;
        failing to submit a usable verdict is an operational error, not a
        substitute judgement about the task.
        """
        workspace = context.workspace_mcp
        git = context.git_mcp
        phase = getattr(context, "phase", "")
        tool_turns = getattr(context, "max_tool_turns", None) or _DEFAULT_MAX_TOOL_TURNS
        completion_tokens = getattr(context, "max_tokens", None) or _DEFAULT_MAX_TOKENS
        # Assemble toolset: intersect declared tools with read-only set,
        # then strip post-execute-only tools if not in post-execute phase.
        declared = set(getattr(self.validator_spec, "tools", []) or [])
        active_names = declared & _READ_ONLY_TOOL_NAMES
        if phase != "post_execute":
            active_names -= _POST_EXECUTE_ONLY_TOOLS

        plugin_definitions, plugin_tools, missing_plugins = offer_definitions(
            configured_tools(self.validator_spec)
        )
        tools = self._build_tool_definitions(active_names) + plugin_definitions
        tools.append(self._SUBMIT_VERDICT_DEF)
        # The produced change reaches the judge through its PHASE, not through
        # a tool grant (Fixes #267): run_validators reads base_ref..HEAD once
        # per pass and shares it on the context; ensure_change_context covers
        # direct calls.  When the protocol granted read_diff_between_refs the
        # judge may still call it — e.g. against another ref — but it never
        # has to, because the change is already in the prompt.
        change = ensure_change_context(context)
        system_prompt = self._build_tool_loop_prompt(
            context, active_names, total_turns=tool_turns,
        )
        # Append outside the overridable prompt builder so acceptance and any
        # other submit_verdict judge receive the same binding in both phases.
        system_prompt += subject_instruction(context.task.id)
        if missing_plugins:
            system_prompt += (
                "\nUnavailable configured validator plugin tools (not installed): "
                + ", ".join(sorted(missing_plugins))
                + ". They were not offered; account for this in your verdict.\n"
            )
        messages: List[Dict[str, Any]] = [{"role": "user", "content": system_prompt}]
        retried_free_text = False
        # Ongoing-work narration goes to the progress sink — never the verdict
        # sink. Wrapping here keeps a raw callback safe on the direct path too;
        # when the runner already wrapped it, the same sink (and its
        # report-once state) is reused.
        cb = ensure_progress_sink(
            getattr(context, "progress_callback", None)
            or getattr(self, "progress_callback", None),
            f"validator {self.validator_spec.validator_id} progress",
        )
        start_time = time.monotonic()
        read_tracker = ReadMemoryTracker(getattr(workspace, "project_root", None))
        # What the judge examined, in order. If the judge fails without ever
        # deciding, this record of how far the inspection got travels on the
        # error result rather than a state invented to hold it.
        examination: List[str] = []
        if change is not None and change.readable and change.diff.strip():
            examination.append(f"prompt: preloaded diff {change.label}")

        # turns_used is what max_tool_turns bounds — it only advances on a
        # turn that made some kind of progress. A turn whose only tool calls
        # were repeat-read hits made none, so it is free (Fixes #360): the
        # judge is not charged for being told to look at a turn it already
        # has. stall_streak counts *consecutive* free turns and forces the
        # verdict-only turn once it reaches _MAX_STALL_TURNS, so a judge that
        # only ever repeats itself is still stopped — sooner than the real
        # budget, rather than looping through it (ADR 050). The raw loop
        # bound below is a defensive ceiling only: every branch that can be
        # reached with is_final_turn or retried_free_text true returns
        # unconditionally, so the loop always terminates well before it.
        # Only progress spends the reading budget. Consecutive repeat-read
        # stalls force the same final verdict-only turn (ADR 050).
        # The raw ceiling also bounds pathological sequences of otherwise free
        # repeated reads without charging productive inspection twice.
        turns_used = 0
        stall_streak = 0
        max_raw_turns = tool_turns * (_MAX_STALL_TURNS + 1) + _MAX_STALL_TURNS + 2
        for turn in range(max_raw_turns):
            is_final_turn = (
                turns_used >= tool_turns - 1 or stall_streak >= _MAX_STALL_TURNS
            )
            # Time is up on the final turn, and it is also up the moment the
            # loop asks the judge for its verdict after a prose answer: the
            # nudge and the final turn are the same moment. The read tools are
            # withdrawn and the judge is asked for a verdict from the evidence
            # it has. A verdict reached on incomplete reading is a real verdict
            # — "warn" exists for exactly that — and the judge is given no way
            # to keep reading (Fixes #285).
            if is_final_turn or retried_free_text:
                offered_names: Set[str] = set()
                turn_tools = [self._SUBMIT_VERDICT_DEF]
                messages.append({"role": "user", "content": _VERDICT_ONLY_INSTRUCTION})
            else:
                offered_names = active_names | set(plugin_tools)
                turn_tools = tools

            turn_start = time.monotonic()
            try:
                # The completion partial binds model and provider credentials as
                # one unit (#237); keep the configured name only for task-scoped
                # header lookup, never as a per-request model override.
                kwargs = {
                    "_configured_model": self.model,
                    "messages": messages,
                    "tools": turn_tools,
                    "max_tokens": completion_tokens,
                    "metadata": {
                        "job_id": self._job_id or "unknown",
                        "task_id": self._task_id or "unknown",
                        "role": f"validator:{self.validator_spec.validator_id}",
                    },
                }
                if not _is_gemini3_plus(self.model):
                    kwargs["temperature"] = 0.0
                # When the offered tools are submit_verdict alone (the final
                # turn, or the turn after prose narration), require that function
                # via tool_choice so the judge is not free to answer in prose a
                # second time (Fixes #296). On reading turns, leave tool_choice
                # unset so the judge is free to read or decide.
                if len(turn_tools) == 1 and turn_tools[0].get("function", {}).get("name") == "submit_verdict":
                    kwargs["tool_choice"] = {
                        "type": "function", "function": {"name": "submit_verdict"},
                    }
                instruction = _UNFORCED_VERDICT_INSTRUCTION if is_final_turn or retried_free_text else None
                response = call_with_unforced_tool_fallback(
                    lambda **request: self._call_completion_with_retry(
                        parameter_fallback_instruction=instruction, **request,
                    ),
                    kwargs,
                    instruction=instruction,
                    on_rejection=lambda error: _logger.warning(
                        "Validator %s provider rejected tool_choice on turn %d "
                        "(model=%s): %s; falling back to unforced request",
                        self.validator_spec.validator_id, turn + 1, self.model, error,
                    ),
                )
            except Exception as e:
                # Provider fault on the tool-loop path: it halts as
                # validator_error, so log the cause and carry its type into
                # the surfaced justification alongside the message.
                _logger.warning(
                    "Validator %s tool-loop hit an operational fault on turn %d "
                    "(model=%s): %s: %s",
                    self.validator_spec.validator_id, turn + 1, self.model,
                    type(e).__name__, e,
                )
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="blocker",
                    justification=(
                        f"LLM tool-loop operational error on turn {turn + 1} "
                        f"({type(e).__name__}): {e}"
                    ),
                    error=True,
                )

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", [])
            if cb:
                elapsed_str = format_elapsed(time.monotonic() - start_time)
                tools_str = format_tool_call_summary(tool_calls)
                cb(f"    [{elapsed_str}] Turn {turn + 1}: {tools_str}")

            # Subject identity is checked before interpreting findings. A wrong
            # subject is terminal, even if the same answer reports missing tool
            # access: that report must not trigger a second penalty or rejudge.
            verdict = self._extract_submit_verdict(tool_calls, expected_task_id=context.task.id)
            if verdict is not None:
                verdict.examined = examination or None
                self._emit_turn_telemetry(
                    turn_index=turn + 1, tool="submit_verdict", target_path="",
                    read_hit=False,
                    tokens_in=_usage_tokens(response, "prompt"),
                    tokens_out=_usage_tokens(response, "completion"),
                    elapsed_ms=(time.monotonic() - turn_start) * 1000,
                    model_requested=self.model, response=response,
                )
                return verdict

            # The judge was offered submit_verdict alone (the final turn, or
            # the turn after the prose nudge). Anything else is a judge that
            # did not decide — an error that fails closed. It is not given
            # another turn, and a read call cannot be honoured: the tools were
            # withdrawn precisely so it would state what it found on what it
            # has.
            if is_final_turn or retried_free_text:
                if tool_calls:
                    attempted = ", ".join(tc.function.name for tc in tool_calls)
                    examination.append(
                        f"turn {turn + 1}: {attempted} (not a verdict; no read "
                        "tools offered where a verdict was requested)"
                    )
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="blocker",
                    justification=(
                        f"Validator did not return a verdict after {turn + 1} "
                        "turn(s): the judge did not call submit_verdict. Fail-closed."
                    ),
                    error=True, examined=examination or None,
                )

            if tool_calls:
                # Preserve the assistant call and a result for every tool ID;
                # providers require a balanced tool transcript on the next turn.
                messages.append({
                    "role": "assistant", "content": msg.content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {
                            "name": tc.function.name, "arguments": tc.function.arguments,
                        }} for tc in tool_calls
                    ],
                })
                # Whether this turn is charged against the budget: a turn
                # whose only tool calls were repeat-read hits made no
                # progress and is free (Fixes #360) — every other outcome
                # (a fresh read, a refusal, an invalid submit_verdict) is
                # unrelated to the repeat-read tracker and still charges as
                # before.
                turn_progressed = False
                for tc in tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    if tool_name == "submit_verdict":
                        # submit_verdict present but not a valid verdict
                        # (invalid severity or unparseable args) — feed back a
                        # tool response so every tool_call_id is answered
                        # before the next request.
                        result = self._submit_verdict_feedback(tc)
                        examination.append(
                            f"turn {turn + 1}: submit_verdict (rejected — invalid arguments)"
                        )
                        turn_progressed = True
                    elif tool_name not in offered_names:
                        # The judge was not granted this tool (or it was
                        # withdrawn on the final turn). A model can only call a
                        # tool it was offered, but the boundary is enforced here
                        # too so a hallucinated or undeclared tool can never
                        # reach the workspace (Fixes #253).
                        available = ", ".join(sorted(offered_names)) or "(none)"
                        result = (
                            f"Tool '{tool_name}' is not available to this "
                            f"validator. Available tools: {available}."
                        )
                        examination.append(
                            f"turn {turn + 1}: {tool_name} (refused — not declared)"
                        )
                        turn_progressed = True
                    else:
                        prev_turn = read_tracker.check_read(tool_name, args)
                        if prev_turn is not None:
                            result = format_repeat_read_response(tool_name, args, prev_turn)
                        else:
                            try:
                                if tool_name in plugin_tools:
                                    result = execute_plugin_tool(
                                        plugin_tools[tool_name], args,
                                        workspace_root=workspace_root(workspace),
                                        spec_text=context.task.spec,
                                        validator_id=self.validator_spec.validator_id,
                                        options=configured_tools(self.validator_spec)[tool_name],
                                        diff_refs=(context.base_ref, "HEAD") if context.base_ref else None,
                                    )
                                else:
                                    result = execute_builtin_tool(tool_name, args, workspace, git)
                            except Exception as e:
                                # Bad arguments are correctable by the judge;
                                # failed inspection infrastructure is not a
                                # finding about the work and must fail closed.
                                if is_tool_argument_error(e):
                                    # A refused path or a missing requested file is
                                    # an ordinary inspection result, not a broken
                                    # tool transport. Preserve the existing
                                    # reasoned-refusal / model-evaluation contract.
                                    result = f"Tool error: {e}"
                                else:
                                    _logger.warning(
                                        "Validator %s declared tool %s failed on turn %d: %s: %s",
                                        self.validator_spec.validator_id, tool_name,
                                        turn + 1, type(e).__name__, e,
                                    )
                                    return ValidatorResult(
                                        validator_id=self.validator_spec.validator_id,
                                        severity="blocker",
                                        justification=(
                                            f"Validator declared tool '{tool_name}' failed "
                                            f"on turn {turn + 1} ({type(e).__name__}): {e}"
                                        ),
                                        error=True, examined=examination or None,
                                    )
                            read_tracker.record_read(tool_name, args, turn + 1)
                            turn_progressed = True
                        target = _normalize_path_arg(args) or json.dumps(args)[:60]
                        examination.append(f"turn {turn + 1}: {tool_name} {target}".rstrip())
                        self._emit_turn_telemetry(
                            turn_index=turn + 1, tool=tool_name,
                            target_path=_normalize_path_arg(args),
                            read_hit=prev_turn is not None,
                            tokens_in=_usage_tokens(response, "prompt"),
                            tokens_out=_usage_tokens(response, "completion"),
                            elapsed_ms=(time.monotonic() - turn_start) * 1000,
                            model_requested=self.model, response=response,
                        )
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": provider_tool_result_text(str(result)),
                    })
                if turn_progressed:
                    turns_used += 1
                    stall_streak = 0
                else:
                    stall_streak += 1
                continue

            # Give narration (including an empty response) one explicit nudge
            # toward the submission tool. Never parse that prose as a verdict.
            has_content = msg.content is not None
            if not has_content and not tool_calls:
                msg.content = ""
                has_content = True
            # A judge that narrated is asked once more to use the tool — and
            # that is the moment the reading window closes. The assistant's
            # prose is recorded here; the next iteration sees
            # retried_free_text set and withdraws the read tools, offering
            # submit_verdict alone with the shared instruction. It is not
            # handed the menu it just chose to keep reading from (Fixes #285).
            if has_content and not retried_free_text:
                retried_free_text = True
                turns_used += 1
                stall_streak = 0
                examination.append(
                    f"turn {turn + 1}: free-text response (not a verdict; asked for submit_verdict)"
                )
                messages.append({"role": "assistant", "content": msg.content})
                continue
            # A judge that still returns nothing is an error, not a verdict.
            # It takes the path errors already take — fail-closed — because an
            # engine that cannot get a verdict out of its quorum must not
            # proceed. What the judge examined before it failed travels with
            # the error result; no state is invented to hold a missing verdict.
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="blocker",
                justification=(
                    f"Validator did not return a verdict after {turn + 1} "
                    "turn(s): the judge did not call submit_verdict. Fail-closed."
                ),
                error=True, examined=examination or None,
            )
        # Unreachable: the final turn always returns above when no verdict is
        # submitted. Kept as the same fail-closed error so a change to the loop
        # bounds can never turn "no verdict" into a pass.
        return ValidatorResult(
            validator_id=self.validator_spec.validator_id,
            severity="blocker",
            justification=(
                f"Validator could not reach a verdict within the "
                f"allocated {tool_turns} turns. Fail-closed."
            ),
            error=True, examined=examination or None,
        )

    def _build_tool_loop_prompt(
        self, context: ValidatorContext, active_names: Set[str],
        total_turns: int = _DEFAULT_MAX_TOOL_TURNS,
    ) -> str:
        """Build the phase-aware judge prompt; subclasses may change the remit.

        Subclasses override this to change what the judge is asked to
        evaluate (e.g. the acceptance validator judges the produced
        artifacts against the task's acceptance criteria instead of
        protocol criteria).
        The change section is derived from the context, not passed in: a
        post-execute judge always begins with the produced change, whatever
        its protocol granted; a pre-execute judge never sees one
        (Fixes #267).

        Subject instructions are appended by the caller so a specialized
        prompt, such as acceptance review, cannot omit the binding contract.
        """
        phase = getattr(context, "phase", "")
        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )
        prompt_parts = [
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n",
            "Evaluate the task against the criteria below.\n",
            "\n", "## Phase\n", f"{_phase_frame(phase)}\n",
            "\n", "## Task\n", f"{context.task.spec}\n",
            "\n", "## Criteria\n", f"{criteria_text}\n",
            change_block_for(context),
        ]
        prompt_parts.extend([
            "\n", "## Available Tools\n",
            "You may call read-only tools to inspect files and git history.\n",
            "When you are ready to deliver your verdict, call the\n",
            "`submit_verdict(task_id, severity, justification)` tool — this is the\n",
            "ONLY way to return your verdict.  Do NOT narrate your verdict\n",
            "as prose; use the tool.\n",
            "\n", "## Tool Budget\n",
            f"You have {total_turns} interaction turn(s) to inspect the repository and deliver a verdict.\n",
            "As you approach this limit, return a verdict based on the evidence you have gathered.\n",
            "An incomplete verdict from a judge who acted responsibly is preferable to running out of turns.\n",
            "\n", "## Instructions\n", "Evaluate against EACH criterion.\n",
            "Use tools to read files if needed.\n",
            tool_access_instruction(self.validator_spec),
            "Then call submit_verdict with severity in [\"pass\", \"warn\", \"blocker\"]\n",
            "and a concise justification.\n",
        ])
        return "".join(prompt_parts)

    @staticmethod
    def _build_tool_definitions(tool_names: Set[str]) -> List[Dict[str, Any]]:
        """Build definitions for exactly the declared read-only tools.

        Build OpenAI-format tool definitions for exactly the declared tools.
        Never returns the full set — only the tools in *tool_names* that
        are in the fixed read-only allowlist.
        """
        all_defs = {
            "read_diff_between_refs": {
                "type": "function", "function": {
                    "name": "read_diff_between_refs",
                    "description": "Read git diff between two refs (e.g. HEAD~1..HEAD)",
                    "parameters": {"type": "object", "properties": {
                        "ref1": {"type": "string", "description": "First ref, e.g. HEAD~1"},
                        "ref2": {"type": "string", "description": "Second ref, e.g. HEAD"},
                    }, "required": ["ref1", "ref2"]},
                },
            },
            "git_show": {
                "type": "function", "function": {
                    "name": "git_show", "description": "Read a file's content at a specific git ref",
                    "parameters": {"type": "object", "properties": {
                        "ref": {"type": "string", "description": "Git ref, e.g. HEAD, main"},
                        "path": {"type": "string", "description": "File path relative to project root"},
                    }, "required": ["ref", "path"]},
                },
            },
            "git_log": {
                "type": "function", "function": {
                    "name": "git_log", "description": "Read recent commits in oneline format",
                    "parameters": {"type": "object", "properties": {
                        "n": {"type": "integer", "description": "Number of commits", "default": 5},
                    }},
                },
            },
            "read_file": {
                "type": "function", "function": {
                    "name": "read_file", "description": "Read full file content",
                    "parameters": {"type": "object", "properties": {
                        "path": {"type": "string", "description": "File path relative to project root"},
                    }, "required": ["path"]},
                },
            },
            "read_file_lines": {
                "type": "function", "function": {
                    "name": "read_file_lines", "description": "Read a line range from a file (1-indexed, inclusive)",
                    "parameters": {"type": "object", "properties": {
                        "path": {"type": "string", "description": "File path relative to project root"},
                        "start": {"type": "integer", "description": "First line number (1-indexed)"},
                        "end": {"type": "integer", "description": "Last line number (1-indexed, inclusive)"},
                    }, "required": ["path", "start", "end"]},
                },
            },
            "list_files": {
                "type": "function", "function": {
                    "name": "list_files", "description": "List files and directories in a directory",
                    "parameters": {"type": "object", "properties": {
                        "directory": {"type": "string", "description": "Directory path", "default": "."},
                    }},
                },
            },
            "summarize_directory": {
                "type": "function", "function": {
                    "name": "summarize_directory",
                    "description": (
                        "Summarize a directory of documents in one call: one "
                        "compact record per file with its path, its heading "
                        "title, and the leading \"Key: value\" lines before the "
                        "first subheading. Does not read document bodies — open "
                        "the few records that matter with read_file. Use this "
                        "instead of listing a directory and reading every file "
                        "to discover which ones are relevant. The result is "
                        "bounded and states when it was truncated."
                    ),
                    "parameters": {"type": "object", "properties": {
                        "directory": {"type": "string", "description": "Directory path", "default": "."},
                    }},
                },
            },
        }
        return [all_defs[name] for name in tool_names if name in all_defs]

    _SUBMIT_VERDICT_DEF = {
        "type": "function",
        "function": {
            "name": "submit_verdict",
            "description": (
                "Submit your final verdict. Call this exactly once when you are "
                "ready to deliver your evaluation. severity must be one of: "
                "pass, warn, blocker."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Exact identifier of the task under judgement, from the prompt",
                    },
                    "severity": {
                        "type": "string", "enum": ["pass", "warn", "blocker"],
                        "description": "Your verdict",
                    },
                    "justification": {
                        "type": "string", "description": "Brief explanation of your evaluation",
                    },
                    "tool_access_missing": {
                        "type": "object",
                        "description": "Required when a criterion needs an unavailable capability",
                        "properties": {
                            "criterion": {"type": "string"}, "capability": {"type": "string"},
                        },
                        "required": ["criterion", "capability"],
                    },
                },
                "required": ["task_id", "severity", "justification"],
            },
        },
    }

    def _extract_submit_verdict(
        self, tool_calls: list, *, expected_task_id: str = "",
    ) -> Optional["ValidatorResult"]:
        """Extract a live verdict, rejecting an unbound subject immediately.

        Subject rejection is terminal, not a malformed-severity correction
        turn. It cannot spend a coder recovery or spec-authoring retry, and
        tool-access reporting from an unusable verdict is not applied again.
        """
        for tc in (tool_calls or []):
            if tc.function.name != "submit_verdict":
                continue
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError) as e:
                # A verdict the judge did deliver is being dropped here
                # (the model is fed generic feedback instead); keep the
                # parse failure's reason reachable.
                _logger.debug(
                    "submit_verdict arguments unparseable for validator %s: %s: %s",
                    self.validator_spec.validator_id, type(e).__name__, e,
                )
                return None
            rejection = reject_wrong_subject(args, expected_task_id, self.validator_spec.validator_id)
            if rejection is not None:
                _logger.warning("Validator %s: %s", self.validator_spec.validator_id, rejection.justification)
                return rejection
            severity = str(args.get("severity", "")).lower().strip()
            justification = str(args.get("justification", "No justification provided"))
            if severity in self.VALID_SEVERITIES:
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity=severity, justification=justification,
                    tool_access_missing=(
                        args.get("tool_access_missing")
                        if isinstance(args.get("tool_access_missing"), dict) else None
                    ),
                )
        return None

    def _submit_verdict_feedback(self, tc: Any) -> str:
        """Answer malformed arguments before a correction request.

        Build a tool response for a submit_verdict call that is not a valid
        verdict (invalid severity or unparseable arguments).

        The response is appended as a tool message so the tool_call_id is
        always answered before the next request.

        Wrong-subject submissions never reach this correction path: returning
        one already ended the loop with an unusable-verdict error.
        """
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}
        severity = str(args.get("severity", "")).lower().strip()
        if severity not in self.VALID_SEVERITIES:
            return (
                f"submit_verdict severity must be one of "
                f"{sorted(self.VALID_SEVERITIES)}, got {severity!r}. "
                "Call submit_verdict(task_id, severity, justification) with a valid severity."
            )
        return (
            "submit_verdict arguments were invalid or unparseable. "
            "Call submit_verdict(task_id, severity, justification) with a valid severity."
        )

    # ------------------------------------------------------------------
    # Single-completion path (pre-execute, unchanged)
    # ------------------------------------------------------------------
    def _build_prompt(self, context_or_task) -> str:
        """Build the legacy JSON prompt, retaining phase and change framing."""
        if isinstance(context_or_task, Task):
            task = context_or_task
            phase = ""
            change_block = ""
        else:
            task = context_or_task.task
            phase = getattr(context_or_task, "phase", "") or ""
            # The produced change reaches a single-completion post-execute
            # judge too — the ones with no tools cannot call anything, so a
            # tool grant could never have helped them (Fixes #267).
            change_block = change_block_for(context_or_task)
        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )
        phase_section = ""
        if phase:
            phase_section = f"\n## Phase\n{_phase_frame(phase)}\n"
        return (
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n"
            f"Evaluate the following task against the criteria below.\n"
            f"\n## Task\n{task.spec}\n{phase_section}"
            f"\n## Criteria\n{criteria_text}\n{change_block}"
            f"\n## Instructions\nEvaluate the task against EACH criterion.\n"
            f"Return your evaluation as a JSON object with exactly two fields:\n"
            f"- \"severity\": one of \"pass\", \"warn\", or \"blocker\"\n"
            f"  - \"pass\" = all criteria satisfied\n"
            f"  - \"warn\" = minor concerns but can proceed\n"
            f"  - \"blocker\" = critical issues that must be addressed\n"
            f"- \"justification\": a brief explanation of your evaluation\n"
            f"\nRespond with ONLY the JSON object, no other text.\n"
            f"\nExample:\n"
            f'{{"severity": "pass", "justification": "Task meets all security criteria."}}\n'
        )

    def _call_completion_with_retry(
        self, parameter_fallback_instruction: Optional[str] = None, **kwargs,
    ) -> Any:
        """Apply provider shape/parameter fallbacks and transient retries.

        Retries each named provider-rejected parameter once, then retries up to
        3 times on transient errors (5xx, 429, connection, DNS,
        timeout). Raises the underlying exception if retries are exhausted or
        the error is not transient (a 4xx other than 429 is a client error —
        retrying it is not honest). The retry sleeps share a 30-second budget
        per validator call so an unavailable provider cannot stall a run
        indefinitely. Provider Retry-After hints take precedence.

        Request compatibility is separate from judging: a provider rejecting
        a parameter can be retried without asking a coder to repair the task.
        Changed judge parameters make the resulting verdict non-cacheable.
        """
        removed: Set[str] = set()
        shaped: Set[str] = set()
        apply_remembered_parameters(self.model, kwargs)
        kwargs["messages"] = apply_remembered_message_shape(kwargs.get("messages", []), self.model)
        while True:
            try:
                return self._call_completion_with_transient_retry(**kwargs)
            except Exception as e:
                if _reshape_rejected_messages(e, kwargs, self.model, shaped):
                    continue
                parameter = _remove_rejected_parameter(e, kwargs, removed, self.model)
                if parameter:
                    self._parameter_fallback_used = True
                    if parameter_fallback_instruction and parameter == "tool_choice":
                        kwargs["messages"].append({"role": "user", "content": parameter_fallback_instruction})
                    _logger.warning(
                        "Validator %s provider rejected parameter %s (model=%s): %s; retrying without it",
                        self.validator_spec.validator_id, parameter, self.model, e,
                    )
                    continue
                raise

    def _call_completion_with_transient_retry(self, **kwargs) -> Any:
        """Retry transient transport faults within attempt and elapsed budgets.

        Respect provider retry hints when present, otherwise use exponential
        backoff with jitter. Deterministic provider rejections are not transient.
        """
        retry_deadline = time.monotonic() + _RETRY_BUDGET_SECONDS
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return self._completion_fn(**kwargs)
            except Exception as e:
                is_transient = _is_transient_error(e)
                if is_transient and attempt < _RETRY_ATTEMPTS - 1:
                    remaining_budget = retry_deadline - time.monotonic()
                    if remaining_budget <= 0:
                        raise
                    provider_delay = _provider_retry_delay(e)
                    if provider_delay is None:
                        exponential_delay = _RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                        jitter_floor = exponential_delay * _RETRY_JITTER_RATIO
                        delay = random.uniform(jitter_floor, exponential_delay)  # noqa: S311
                    else:
                        delay = provider_delay
                    delay = min(delay, remaining_budget)
                    _logger.warning(
                        "Transient LLM provider error on attempt %d for validator %s: %s; retrying in %.2fs...",
                        attempt + 1, self.validator_spec.validator_id, e, delay,
                    )
                    time.sleep(delay)
                else:
                    raise

    def _call_llm(self, prompt: str) -> str:
        """Request the legacy free-text JSON response with bounded diagnostics.

        Call the LLM for a single completion.
        Args:
            prompt: The judge prompt
        Returns:
            Raw response text from the LLM
        Raises:
            Exception: If the LLM call fails
        """
        # The completion partial binds model and api_base together (#237).
        kwargs = {
            # No "model" here on purpose: the completion function is a partial
            # with model AND api_base already bound together (#237). Passing a
            # model kwarg overrides the bound model without its api_base, and
            # the raw configured name ("ocgo/...") is not a litellm provider.
            # _configured_model is popped by the header wrapper and never
            # reaches litellm.
            "_configured_model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.completion_tokens,
            "metadata": {
                "job_id": self._job_id or "unknown", "task_id": self._task_id or "unknown",
                "role": f"validator:{self.validator_spec.validator_id}",
            },
        }
        if not _is_gemini3_plus(self.model):
            kwargs["temperature"] = 0.0
        response = self._call_completion_with_retry(**kwargs)
        content = response.choices[0].message.content
        if not content:
            _logger.warning("Validator %s returned empty response (model=%s)", self.validator_spec.validator_id, self.model)
        else:
            _logger.debug("Validator %s raw response (first 2KB): %s", self.validator_spec.validator_id, _truncated_log(content))
        return content

    def _call_llm_structured(self, prompt: str) -> ValidatorResult:
        """Use the provider's response schema and restore the configured judge ID.

        Call the LLM with response_format=ValidatorResult for structured output.
        LiteLLM enforces JSON schema at the API level.  The response content
        is guaranteed to be valid JSON matching the ValidatorResult schema.
        Zero free-text parsing.
        """
        kwargs = {
            # No "model" here on purpose: the completion function is a partial
            # with model AND api_base already bound together (#237). Passing a
            # model kwarg overrides the bound model without its api_base, and
            # the raw configured name ("ocgo/...") is not a litellm provider.
            # _configured_model is popped by the header wrapper and never
            # reaches litellm.
            "_configured_model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.completion_tokens,
            "response_format": ValidatorResult,
            "metadata": {
                "job_id": self._job_id or "unknown", "task_id": self._task_id or "unknown",
                "role": f"validator:{self.validator_spec.validator_id}",
            },
        }
        if not _is_gemini3_plus(self.model):
            kwargs["temperature"] = 0.0
        response = self._call_completion_with_retry(**kwargs)
        content = response.choices[0].message.content
        if not content:
            _logger.warning("Validator %s returned empty structured response (model=%s)", self.validator_spec.validator_id, self.model)
        else:
            _logger.debug("Validator %s raw response (first 2KB): %s", self.validator_spec.validator_id, _truncated_log(content))
        result = ValidatorResult.model_validate_json(content)
        return result.model_copy(update={"validator_id": self.validator_spec.validator_id})

    def _parse_response(self, response_text: str) -> ValidatorResult:
        """Parse the legacy response, including fenced or embedded JSON.

        Attempts JSON parsing, with fallback regex extraction.
        Falls back to "warn" if parsing fails entirely.
        Args:
            response_text: Raw LLM response text
        Returns:
            ValidatorResult with parsed severity and justification

        Its historical warn fallback is not used for tool-loop answers. Live
        submit_verdict submissions go through the exact subject check instead.
        """
        parsed = self._try_json_parse(response_text)
        if parsed is None:
            parsed = self._try_extract_json(response_text)
        if parsed is None:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id, severity="warn",
                justification=f"Could not parse LLM response: {response_text[:200]}",
            )
        severity = str(parsed.get("severity", "")).lower().strip()
        justification = str(parsed.get("justification", "No justification provided"))
        if severity not in self.VALID_SEVERITIES:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id, severity="warn",
                justification=f"Invalid severity '{severity}' from LLM. {justification}",
            )
        return ValidatorResult(
            validator_id=self.validator_spec.validator_id, severity=severity,
            justification=justification,
        )

    def _try_json_parse(self, text: str) -> Optional[dict]:
        """Try the whole response first, retaining the parse failure in debug logs."""
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError) as e:
            _logger.debug(
                "Validator %s: direct JSON parse failed: %s: %s",
                self.validator_spec.validator_id, type(e).__name__, e,
            )
            return None

    def _try_extract_json(self, text: str) -> Optional[dict]:
        """Recover legacy JSON wrapped in markdown or surrounding explanation."""
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError) as e:
                _logger.debug(
                    "Validator %s: code-block JSON parse failed: %s: %s",
                    self.validator_spec.validator_id, type(e).__name__, e,
                )
        match = re.search(r'\{[^{}]*"severity"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError) as e:
                _logger.debug(
                    "Validator %s: embedded-object JSON parse failed: %s: %s",
                    self.validator_spec.validator_id, type(e).__name__, e,
                )
        return None


def _truncated_log(raw: str, max_chars: int = 2048) -> str:
    """Bound raw-response diagnostics without changing the response we parse."""
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "...<truncated>"


_default_registry.register_compound(LLMValidator.HANDLED_TYPES, LLMValidator)
