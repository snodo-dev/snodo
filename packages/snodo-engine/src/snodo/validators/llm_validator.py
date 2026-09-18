"""LLM Validator - AI-driven pre-execute validation.

FILE: snodo/validators/llm_validator.py (Task 6.1)

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

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.change import change_block_for, ensure_change_context
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import _default_registry
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.coders.litellm import ReadMemoryTracker, _normalize_path_arg, format_repeat_read_response

_litellm.drop_params = True

_logger = logging.getLogger(__name__)


# Maximum tool-use turns before forcing a verdict.
_DEFAULT_MAX_TOOL_TURNS = 20
_DEFAULT_MAX_TOKENS = 1500
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_BUDGET_SECONDS = 30.0
_RETRY_JITTER_RATIO = 0.5

# Fixed read-only tool names — the only tools a validator may ever use.
_READ_ONLY_TOOL_NAMES: Set[str] = {
    "read_file",
    "read_file_lines",
    "list_files",
    "git_show",
    "git_log",
    "read_diff_between_refs",
    "summarize_directory",
}

# Tools only meaningful when a change is committed (post-execute).
_POST_EXECUTE_ONLY_TOOLS: Set[str] = {"read_diff_between_refs"}

# The instruction that closes the reading window. The nudge after a prose
# answer and the final turn are the same moment — the system has decided the
# reading is over — so they say the same thing: the read tools are withdrawn,
# decide now, and a verdict on a partial view is real ("warn" exists for it).
_VERDICT_ONLY_INSTRUCTION = (
    "The read tools are no longer available. Return your verdict now by "
    "calling submit_verdict(severity, justification); do not narrate. A "
    "verdict reached on partial reading is a real verdict — if your view is "
    "partial, say so in the justification and use \"warn\"."
)


def _phase_frame(phase: str) -> str:
    """Return the phase statement that tells the judge what it is looking at.

    The same criteria list reads differently depending on phase: at
    pre-execute the judge is reviewing a proposal (the described work does not
    exist yet, and its absence is never a finding); at post-execute it is
    inspecting a finished result (absence of the described work *is* a
    finding).  Without this frame, a tool-enabled pre-execute judge reads
    "evaluate the task against the criteria" as "check whether this was done"
    and blocks on work that cannot exist yet (see ADR 019).
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


def _is_gemini3_plus(model: str) -> bool:
    m = re.search(r'gemini-(\d+)', model)
    return bool(m and int(m.group(1)) >= 3)


def _is_transient_error(e: Exception) -> bool:
    """Return True if *e* is a transient provider/network error worth retrying.

    Classifies on exception type and HTTP status code, not on error prose.
    The previous predicate substring-matched the message against terms like
    ``"500"``, ``"502"`` and ``"deepseekexception"``, so every error from a
    provider whose name contained those letters was retryable, and a bare
    status code matched those digits anywhere in the text. A 4xx (except 429)
    is a client error — retrying it is not honest; a 5xx, 429, connection or
    timeout is transient.
    """
    # Network-level builtins: genuinely transient.
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    # DNS resolution failure (errno 8: nodename nor servname).
    if isinstance(e, OSError) and getattr(e, "errno", None) == 8:
        return True

    # litellm exception classes.
    try:
        from litellm.exceptions import (
            APIConnectionError,
            Timeout as LiteLLMTimeout,
            RateLimitError,
            InternalServerError,
            BadGatewayError,
            ServiceUnavailableError,
        )
        if isinstance(
            e,
            (APIConnectionError, LiteLLMTimeout, RateLimitError,
             InternalServerError, BadGatewayError, ServiceUnavailableError),
        ):
            return True
    except ImportError as e:
        # The classifier's own degradation must be visible: without these
        # classes a transient connection error is judged only by status code,
        # and a caller that carries on should be able to see why the
        # classification changed shape.
        _logger.debug(
            "litellm exception classes unavailable, transient check falls "
            "back to status code: %s", e,
        )

    # Fall back to the HTTP status code when the exception carries one.
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return status in (429, 500, 502, 503, 504)
    return False


def _is_provider_rejection(e: Exception) -> bool:
    """Return True if *e* is a provider rejecting the request (a 4xx client error).

    Used to distinguish "the provider refused response_format" (a 400 like
    DeepSeek's "This response_format type is unavailable now") or forced
    tool_choice from "the model returned garbage" — only the former makes an
    unparseable fallback an operational fault rather than a warn verdict (Fixes #84, #296).
    """
    try:
        from litellm.exceptions import (
            BadRequestError,
            InvalidRequestError,
            UnsupportedParamsError,
        )
        if isinstance(e, (BadRequestError, InvalidRequestError, UnsupportedParamsError)):
            return True
    except ImportError:
        pass
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status != 429
    return False


def _provider_retry_delay(e: Exception) -> Optional[float]:
    """Return a provider-supplied retry delay, if the exception carries one."""
    candidates = [getattr(e, "retry_after", None)]
    headers = getattr(e, "headers", None)
    response = getattr(e, "response", None)
    response_headers = getattr(response, "headers", None)
    for header_map in (headers, response_headers):
        if header_map is not None:
            candidates.append(header_map.get("retry-after"))
            candidates.append(header_map.get("Retry-After"))

    for value in candidates:
        try:
            delay = float(value)
        except (TypeError, ValueError):
            continue
        if delay >= 0:
            return delay
    return None


def _usage_tokens(response: Any, kind: str) -> int:
    """Extract prompt/completion token counts from a litellm response.

    Returns 0 when the response carries no usage (e.g. mock responses).
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0
        if kind == "prompt":
            return int(getattr(usage, "prompt_tokens", 0) or 0)
        return int(getattr(usage, "completion_tokens", 0) or 0)
    except Exception:
        return 0


class LLMValidator(ValidatorBase):
    """Evaluates tasks against protocol criteria using an LLM judge."""

    #: A single-completion judge of the task spec.  This is the pre-execute
    #: answer; the runner forces every post-execute judge (including a
    #: subclass that leaves this inherited) onto the tree subject (#246).
    cache_subject = "spec"

    VALID_SEVERITIES = {"pass", "warn", "blocker"}

    HANDLED_TYPES = {
        "architecture", "security", "conventions",
        "performance", "testing", "planning",
    }

    def __init__(
        self,
        validator_spec: Validator,
        completion_fn=None,
        model: str = DEFAULT_MODEL,
    ):
        self.validator_spec = validator_spec
        self._completion_fn = completion_fn
        self.model = model
        self.completion_tokens = _DEFAULT_MAX_TOKENS
        self._job_id: str = ""
        self._task_id: str = ""
        self._depth: int = 0
        self._attempt: int = 1

    def _emit_turn_telemetry(
        self,
        turn_index: int,
        tool: str,
        target_path: str,
        read_hit: bool,
        tokens_in: int,
        tokens_out: int,
        elapsed_ms: float,
    ) -> None:
        """Emit one per-turn telemetry record to the job's state.json.

        Operational telemetry, not part of the audit chain (ADR 034). Never
        raises — telemetry must not crash the tool loop.
        """
        try:
            from snodo.infrastructure.tool_telemetry import (
                canonical_target_path,
                persist_tool_telemetry,
            )

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
        # Backward-compat: accept Task for old test code
        if isinstance(context_or_task, Task):
            context = ValidatorContext(
                task=context_or_task,
                completion_fn=self._completion_fn,
                model=self.model,
            )
        else:
            context = context_or_task
            # Prefer context-provided values over instance defaults
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
        if (
            declared_tools
            and context.workspace_mcp is not None
            and context.git_mcp is not None
            and self._completion_fn is not None
        ):
            res = self._evaluate_with_tools(context)
            return enrich_result_with_criteria(res, getattr(self.validator_spec, "criteria", []))

        # Pre-execute or fallback: single-completion path
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
                return enrich_result_with_criteria(res, getattr(self.validator_spec, "criteria", []))
            except Exception as e:
                structured_rejected = _is_provider_rejection(e)

        # Legacy: free-text completion + hand-rolled parse
        if self._completion_fn is None:
            res = ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="blocker",
                justification="No completion_fn available",
                error=True,
            )
            return enrich_result_with_criteria(res, getattr(self.validator_spec, "criteria", []))
        try:
            response_text = self._call_llm(prompt)
            res = self._parse_response(response_text)
            # If the provider rejected structured output AND the unstructured
            # fallback did not parse, that is an operational fault, not a
            # verdict — the provider refused the structured call and the
            # fallback yielded nothing usable (Fixes #84).
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
        return enrich_result_with_criteria(res, getattr(self.validator_spec, "criteria", []))

    # ------------------------------------------------------------------
    # Post-execute bounded tool-use loop
    # ------------------------------------------------------------------

    def _evaluate_with_tools(self, context: ValidatorContext) -> ValidatorResult:
        """Run a bounded read-only tool-use loop.

        Activated by declared tools on the validator spec (not phase).
        Phase filters read_diff_between_refs (meaningful post-execute only)
        and reaches the prompt via ``_phase_frame``, so the judge knows
        whether it is reviewing a proposal or inspecting a result.
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

        tools = self._build_tool_definitions(active_names)
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

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": system_prompt},
        ]

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

        for turn in range(tool_turns):
            is_final_turn = turn == tool_turns - 1
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
                messages.append({
                    "role": "user",
                    "content": _VERDICT_ONLY_INSTRUCTION,
                })
            else:
                offered_names = active_names
                turn_tools = tools

            turn_start = time.monotonic()
            try:
                from snodo.config import ConfigManager
                kwargs = {
                    "model": ConfigManager.resolve_litellm_model(self.model),
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
                        "type": "function",
                        "function": {"name": "submit_verdict"},
                    }
                try:
                    response = self._call_completion_with_retry(**kwargs)
                except Exception as e:
                    # Not all providers honour a forced function choice; if
                    # rejected for that reason (4xx client error), fall back to
                    # the unforced request rather than failing as an operational
                    # error (Fixes #296).
                    if "tool_choice" in kwargs and _is_provider_rejection(e):
                        _logger.warning(
                            "Validator %s provider rejected tool_choice on turn %d "
                            "(model=%s): %s; falling back to unforced request",
                            self.validator_spec.validator_id, turn + 1, self.model, e,
                        )
                        del kwargs["tool_choice"]
                        response = self._call_completion_with_retry(**kwargs)
                    else:
                        raise
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

            # Check for submit_verdict before anything else
            verdict = self._extract_submit_verdict(tool_calls)
            if verdict is not None:
                self._emit_turn_telemetry(
                    turn_index=turn + 1,
                    tool="submit_verdict",
                    target_path="",
                    read_hit=False,
                    tokens_in=_usage_tokens(response, "prompt"),
                    tokens_out=_usage_tokens(response, "completion"),
                    elapsed_ms=(time.monotonic() - turn_start) * 1000,
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
                    attempted = ", ".join(
                        tc.function.name for tc in tool_calls
                    )
                    examination.append(
                        f"turn {turn + 1}: {attempted} (not a verdict; no read "
                        "tools offered where a verdict was requested)"
                    )
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="blocker",
                    justification=(
                        f"Validator did not return a verdict after {turn + 1} "
                        "turn(s): the judge did not call submit_verdict. "
                        "Fail-closed."
                    ),
                    error=True,
                    examined=examination or None,
                )

            # If any tool calls (read tools), execute them and continue
            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })

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
                    else:
                        prev_turn = read_tracker.check_read(tool_name, args)
                        if prev_turn is not None:
                            result = format_repeat_read_response(tool_name, args, prev_turn)
                        else:
                            result = self._execute_tool(tool_name, args, workspace, git)
                            read_tracker.record_read(tool_name, args, turn + 1)
                        target = _normalize_path_arg(args) or json.dumps(args)[:60]
                        examination.append(f"turn {turn + 1}: {tool_name} {target}".rstrip())
                        self._emit_turn_telemetry(
                            turn_index=turn + 1,
                            tool=tool_name,
                            target_path=_normalize_path_arg(args),
                            read_hit=prev_turn is not None,
                            tokens_in=_usage_tokens(response, "prompt"),
                            tokens_out=_usage_tokens(response, "completion"),
                            elapsed_ms=(time.monotonic() - turn_start) * 1000,
                        )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result),
                    })
                continue

            # No submit_verdict — either free-text or empty response
            has_content = msg.content is not None
            if not has_content and not tool_calls:
                msg.content = ""  # normalise so the retry path picks it up
                has_content = True

            # A judge that narrated is asked once more to use the tool — and
            # that is the moment the reading window closes. The assistant's
            # prose is recorded here; the next iteration sees
            # retried_free_text set and withdraws the read tools, offering
            # submit_verdict alone with the shared instruction. It is not
            # handed the menu it just chose to keep reading from (Fixes #285).
            if has_content and not retried_free_text:
                retried_free_text = True
                examination.append(
                    f"turn {turn + 1}: free-text response (not a verdict; asked for submit_verdict)"
                )
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                })
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
                    "turn(s): the judge did not call submit_verdict. "
                    "Fail-closed."
                ),
                error=True,
                examined=examination or None,
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
            error=True,
            examined=examination or None,
        )

    def _build_tool_loop_prompt(
        self,
        context: ValidatorContext,
        active_names: Set[str],
        total_turns: int = _DEFAULT_MAX_TOOL_TURNS,
    ) -> str:
        """Build the tool-loop judge prompt for this validator.

        Subclasses override this to change what the judge is asked to
        evaluate (e.g. the acceptance validator judges the produced
        artifacts against the task's acceptance criteria instead of
        protocol criteria).

        The change section is derived from the context, not passed in: a
        post-execute judge always begins with the produced change, whatever
        its protocol granted; a pre-execute judge never sees one
        (Fixes #267).
        """
        phase = getattr(context, "phase", "")
        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )

        prompt_parts = [
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n",
            "Evaluate the task against the criteria below.\n",
            "\n",
            "## Phase\n",
            f"{_phase_frame(phase)}\n",
            "\n",
            "## Task\n",
            f"{context.task.spec}\n",
            "\n",
            "## Criteria\n",
            f"{criteria_text}\n",
            change_block_for(context),
        ]

        prompt_parts.extend([
            "\n",
            "## Available Tools\n",
            "You may call read-only tools to inspect files and git history.\n",
            "When you are ready to deliver your verdict, call the\n",
            "`submit_verdict(severity, justification)` tool — this is the\n",
            "ONLY way to return your verdict.  Do NOT narrate your verdict\n",
            "as prose; use the tool.\n",
            "\n",
            "## Tool Budget\n",
            f"You have {total_turns} interaction turn(s) to inspect the repository and deliver a verdict.\n",
            "As you approach this limit, return a verdict based on the evidence you have gathered.\n",
            "An incomplete verdict from a judge who acted responsibly is preferable to running out of turns.\n",
            "\n",
            "## Instructions\n",
            "Evaluate against EACH criterion.\n",
            "Use tools to read files if needed.\n",
            "Then call submit_verdict with severity in [\"pass\", \"warn\", \"blocker\"]\n",
            "and a concise justification.\n",
        ])

        return "".join(prompt_parts)

    @staticmethod
    def _build_tool_definitions(tool_names: Set[str]) -> List[Dict[str, Any]]:
        """Build OpenAI-format tool definitions for exactly the declared tools.

        Never returns the full set — only the tools in *tool_names* that
        are in the fixed read-only allowlist.
        """
        all_defs = {
            "read_diff_between_refs": {
                "type": "function",
                "function": {
                    "name": "read_diff_between_refs",
                    "description": "Read git diff between two refs (e.g. HEAD~1..HEAD)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref1": {"type": "string", "description": "First ref, e.g. HEAD~1"},
                            "ref2": {"type": "string", "description": "Second ref, e.g. HEAD"},
                        },
                        "required": ["ref1", "ref2"],
                    },
                },
            },
            "git_show": {
                "type": "function",
                "function": {
                    "name": "git_show",
                    "description": "Read a file's content at a specific git ref",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string", "description": "Git ref, e.g. HEAD, main"},
                            "path": {"type": "string", "description": "File path relative to project root"},
                        },
                        "required": ["ref", "path"],
                    },
                },
            },
            "git_log": {
                "type": "function",
                "function": {
                    "name": "git_log",
                    "description": "Read recent commits in oneline format",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "n": {"type": "integer", "description": "Number of commits", "default": 5},
                        },
                    },
                },
            },
            "read_file": {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read full file content",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path relative to project root"},
                        },
                        "required": ["path"],
                    },
                },
            },
            "read_file_lines": {
                "type": "function",
                "function": {
                    "name": "read_file_lines",
                    "description": "Read a line range from a file (1-indexed, inclusive)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path relative to project root"},
                            "start": {"type": "integer", "description": "First line number (1-indexed)"},
                            "end": {"type": "integer", "description": "Last line number (1-indexed, inclusive)"},
                        },
                        "required": ["path", "start", "end"],
                    },
                },
            },
            "list_files": {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files and directories in a directory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string", "description": "Directory path", "default": "."},
                        },
                    },
                },
            },
            "summarize_directory": {
                "type": "function",
                "function": {
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
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string", "description": "Directory path", "default": "."},
                        },
                    },
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
                    "severity": {
                        "type": "string",
                        "enum": ["pass", "warn", "blocker"],
                        "description": "Your verdict",
                    },
                    "justification": {
                        "type": "string",
                        "description": "Brief explanation of your evaluation",
                    },
                },
                "required": ["severity", "justification"],
            },
        },
    }

    def _extract_submit_verdict(self, tool_calls: list) -> Optional["ValidatorResult"]:
        """Scan tool_calls for submit_verdict and return a ValidatorResult if found.

        Returns None if submit_verdict is not present or has invalid arguments.
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
            severity = str(args.get("severity", "")).lower().strip()
            justification = str(args.get("justification", "No justification provided"))
            if severity in self.VALID_SEVERITIES:
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity=severity,
                    justification=justification,
                )
        return None

    def _submit_verdict_feedback(self, tc: Any) -> str:
        """Build a tool response for a submit_verdict call that is not a valid
        verdict (invalid severity or unparseable arguments).

        The response is appended as a tool message so the tool_call_id is
        always answered before the next request.
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
                "Call submit_verdict(severity, justification) with a valid "
                "severity."
            )
        return (
            "submit_verdict arguments were invalid or unparseable. "
            "Call submit_verdict(severity, justification) with a valid "
            "severity."
        )

    @staticmethod
    def _execute_tool(
        name: str,
        args: Dict[str, Any],
        workspace: Any,
        git: Any,
    ) -> str:
        """Execute a read-only tool call and return the result as a string."""
        try:
            if name == "read_diff_between_refs":
                return git.diff_between_refs(args["ref1"], args["ref2"])
            elif name == "git_show":
                return git.show(args["ref"], args["path"])
            elif name == "git_log":
                return git.log(args.get("n", 5))
            elif name == "read_file":
                return workspace.read_file(args["path"])
            elif name == "read_file_lines":
                return workspace.read_file_lines(args["path"], args["start"], args["end"])
            elif name == "list_files":
                return "\n".join(workspace.list_files(args.get("directory", ".")))
            elif name == "summarize_directory":
                return workspace.summarize_directory(args.get("directory", "."))
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            return f"Tool error: {e}"

    # ------------------------------------------------------------------
    # Single-completion path (pre-execute, unchanged)
    # ------------------------------------------------------------------

    def _build_prompt(self, context_or_task) -> str:
        # Backward compat: accept Task directly for old test code
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
            phase_section = (
                f"\n"
                f"## Phase\n"
                f"{_phase_frame(phase)}\n"
            )

        return (
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n"
            f"Evaluate the following task against the criteria below.\n"
            f"\n"
            f"## Task\n"
            f"{task.spec}\n"
            f"{phase_section}"
            f"\n"
            f"## Criteria\n"
            f"{criteria_text}\n"
            f"{change_block}"
            f"\n"
            f"## Instructions\n"
            f"Evaluate the task against EACH criterion.\n"
            f"Return your evaluation as a JSON object with exactly two fields:\n"
            f"- \"severity\": one of \"pass\", \"warn\", or \"blocker\"\n"
            f"  - \"pass\" = all criteria satisfied\n"
            f"  - \"warn\" = minor concerns but can proceed\n"
            f"  - \"blocker\" = critical issues that must be addressed\n"
            f"- \"justification\": a brief explanation of your evaluation\n"
            f"\n"
            f"Respond with ONLY the JSON object, no other text.\n"
            f"\n"
            f"Example:\n"
            f'{{"severity": "pass", "justification": "Task meets all security criteria."}}\n'
        )

    def _call_completion_with_retry(self, **kwargs) -> Any:
        """Call completion_fn with retries for transient provider/network errors.

        Retries up to 3 times on transient errors (5xx, 429, connection, DNS,
        timeout). Raises the underlying exception if retries are exhausted or
        the error is not transient (a 4xx other than 429 is a client error —
        retrying it is not honest). The retry sleeps share a 30-second budget
        per validator call so an unavailable provider cannot stall a run
        indefinitely. Provider Retry-After hints take precedence.
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
                        delay = random.uniform(jitter_floor, exponential_delay)  # noqa: S311 - retry jitter is not security-sensitive
                    else:
                        delay = provider_delay
                    delay = min(delay, remaining_budget)
                    _logger.warning(
                        "Transient LLM provider error on attempt %d for validator %s: %s; retrying in %.2fs...",
                        attempt + 1,
                        self.validator_spec.validator_id,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    raise

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM for a single completion.

        Args:
            prompt: The judge prompt

        Returns:
            Raw response text from the LLM

        Raises:
            Exception: If the LLM call fails
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
            "metadata": {
                "job_id": self._job_id or "unknown",
                "task_id": self._task_id or "unknown",
                "role": f"validator:{self.validator_spec.validator_id}",
            },
        }
        if not _is_gemini3_plus(self.model):
            kwargs["temperature"] = 0.0
        response = self._call_completion_with_retry(**kwargs)
        content = response.choices[0].message.content
        if not content:
            _logger.warning(
                "Validator %s returned empty response (model=%s)",
                self.validator_spec.validator_id, self.model,
            )
        else:
            _logger.debug(
                "Validator %s raw response (first 2KB): %s",
                self.validator_spec.validator_id, _truncated_log(content),
            )
        return content

    def _call_llm_structured(self, prompt: str) -> ValidatorResult:
        """Call the LLM with response_format=ValidatorResult for structured output.

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
                "job_id": self._job_id or "unknown",
                "task_id": self._task_id or "unknown",
                "role": f"validator:{self.validator_spec.validator_id}",
            },
        }
        if not _is_gemini3_plus(self.model):
            kwargs["temperature"] = 0.0
        response = self._call_completion_with_retry(**kwargs)
        content = response.choices[0].message.content
        if not content:
            _logger.warning(
                "Validator %s returned empty structured response (model=%s)",
                self.validator_spec.validator_id, self.model,
            )
        else:
            _logger.debug(
                "Validator %s raw response (first 2KB): %s",
                self.validator_spec.validator_id, _truncated_log(content),
            )
        return ValidatorResult.model_validate_json(content)

    def _parse_response(self, response_text: str) -> ValidatorResult:
        """Parse LLM response into a ValidatorResult.

        Attempts JSON parsing, with fallback regex extraction.
        Falls back to "warn" if parsing fails entirely.

        Args:
            response_text: Raw LLM response text

        Returns:
            ValidatorResult with parsed severity and justification
        """
        # Try direct JSON parse first
        parsed = self._try_json_parse(response_text)

        if parsed is None:
            # Try extracting JSON from markdown code blocks or mixed text
            parsed = self._try_extract_json(response_text)

        if parsed is None:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"Could not parse LLM response: {response_text[:200]}",
            )

        severity = str(parsed.get("severity", "")).lower().strip()
        justification = str(parsed.get("justification", "No justification provided"))

        # Validate severity
        if severity not in self.VALID_SEVERITIES:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"Invalid severity '{severity}' from LLM. {justification}",
            )

        return ValidatorResult(
            validator_id=self.validator_spec.validator_id,
            severity=severity,
            justification=justification,
        )

    def _try_json_parse(self, text: str) -> Optional[dict]:
        """Try to parse text as JSON directly."""
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError) as e:
            _logger.debug(
                "Validator %s: direct JSON parse failed: %s: %s",
                self.validator_spec.validator_id, type(e).__name__, e,
            )
            return None

    def _try_extract_json(self, text: str) -> Optional[dict]:
        """Try to extract JSON from text with surrounding content."""
        # Try code block extraction
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError) as e:
                _logger.debug(
                    "Validator %s: code-block JSON parse failed: %s: %s",
                    self.validator_spec.validator_id, type(e).__name__, e,
                )

        # Try finding JSON object in text
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
    """Truncate a raw response string for logging."""
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "...<truncated>"


_default_registry.register_compound(LLMValidator.HANDLED_TYPES, LLMValidator)
