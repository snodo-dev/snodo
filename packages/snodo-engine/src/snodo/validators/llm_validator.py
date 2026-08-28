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
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

from snodo.engine.progress import format_elapsed, format_tool_call_summary

from litellm import supports_response_schema

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import _default_registry
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.coders.litellm import ReadMemoryTracker, format_repeat_read_response

_logger = logging.getLogger(__name__)


# Maximum tool-use turns before forcing a verdict.
_DEFAULT_MAX_TOOL_TURNS = 20
_DEFAULT_MAX_TOKENS = 1500

# Fixed read-only tool names — the only tools a validator may ever use.
_READ_ONLY_TOOL_NAMES: Set[str] = {
    "read_file",
    "read_file_lines",
    "list_files",
    "git_show",
    "git_log",
    "read_diff_between_refs",
}

# Tools only meaningful when a change is committed (post-execute).
_POST_EXECUTE_ONLY_TOOLS: Set[str] = {"read_diff_between_refs"}


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
    except ImportError:
        pass

    # Fall back to the HTTP status code when the exception carries one.
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return status in (429, 500, 502, 503, 504)
    return False


def _is_provider_rejection(e: Exception) -> bool:
    """Return True if *e* is a provider rejecting the request (a 4xx client error).

    Used to distinguish "the provider refused response_format" (a 400 like
    DeepSeek's "This response_format type is unavailable now") from "the model
    returned garbage" — only the former makes an unparseable fallback an
    operational fault rather than a warn verdict (Fixes #84).
    """
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status != 429
    return False


class LLMValidator(ValidatorBase):
    """Evaluates tasks against protocol criteria using an LLM judge."""

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

    def _resolve_extra_headers(self) -> Optional[dict]:
        """Return extra_headers for the model's provider."""
        from snodo.config import ConfigManager
        return ConfigManager.resolve_extra_headers(self.model, task_id=self._task_id)

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
            res = ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="blocker",
                justification=f"LLM validation failed due to operational error: {e}",
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

        # Only prepend diff when the diff tool is in the active set. Prefer the
        # execute-node HEAD anchor (base_ref..HEAD); fall back to HEAD~1..HEAD
        # only when the anchor is absent, and say so in the prompt the judge
        # sees — a judge reviewing a fallback range should know it.
        has_diff = "read_diff_between_refs" in active_names
        change_diff = ""
        diff_label = ""
        diff_is_fallback = False
        if has_diff:
            base_ref = getattr(context, "base_ref", None)
            try:
                if base_ref:
                    diff_label = f"{base_ref}..HEAD"
                    change_diff = git.diff_between_refs(base_ref, "HEAD")
                else:
                    diff_label = "HEAD~1..HEAD"
                    change_diff = git.diff_between_refs("HEAD~1", "HEAD")
                    diff_is_fallback = True
            except Exception:
                change_diff = f"(unable to read diff {diff_label or 'HEAD~1..HEAD'})"

        system_prompt = self._build_tool_loop_prompt(
            context, active_names, has_diff, change_diff,
            diff_label=diff_label, diff_is_fallback=diff_is_fallback,
        )

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": system_prompt},
        ]

        retried_free_text = False
        cb = getattr(context, "progress_callback", None) or getattr(self, "progress_callback", None)
        start_time = time.monotonic()
        read_tracker = ReadMemoryTracker()

        for turn in range(tool_turns):
            try:
                from snodo.config import ConfigManager
                kwargs = {
                    "model": ConfigManager.resolve_litellm_model(self.model),
                    "messages": messages,
                    "tools": tools,
                    "max_tokens": completion_tokens,
                    "metadata": {
                        "job_id": self._job_id or "unknown",
                        "task_id": self._task_id or "unknown",
                        "role": f"validator:{self.validator_spec.validator_id}",
                    },
                }
                if not _is_gemini3_plus(self.model):
                    kwargs["temperature"] = 0.0
                extra_headers = self._resolve_extra_headers()
                if extra_headers:
                    kwargs["extra_headers"] = extra_headers
                response = self._call_completion_with_retry(**kwargs)
            except Exception as e:
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="blocker",
                    justification=f"LLM tool-loop operational error on turn {turn + 1}: {e}",
                    error=True,
                )

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", [])

            if cb:
                elapsed_str = format_elapsed(time.monotonic() - start_time)
                tools_str = format_tool_call_summary(tool_calls)
                try:
                    cb(f"    [{elapsed_str}] Turn {turn + 1}: {tools_str}")
                except Exception:
                    pass

            # Check for submit_verdict before anything else
            verdict = self._extract_submit_verdict(tool_calls)
            if verdict is not None:
                return verdict

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
                    else:
                        prev_turn = read_tracker.check_read(tool_name, args)
                        if prev_turn is not None:
                            result = format_repeat_read_response(tool_name, args, prev_turn)
                        else:
                            result = self._execute_tool(tool_name, args, workspace, git)
                            read_tracker.record_read(tool_name, args, turn + 1)

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

            if has_content and not retried_free_text:
                retried_free_text = True
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "Return your verdict by calling "
                        "submit_verdict(severity, justification). "
                        "Do not narrate."
                    ),
                })
                continue

            # Still no valid verdict after retry — fail closed
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="blocker",
                justification=(
                    f"Validator did not call submit_verdict after {turn + 1} turn(s). "
                    "No reliable verdict could be obtained."
                ),
                error=True,
            )

        # Hit the turn cap — fail closed
        return ValidatorResult(
            validator_id=self.validator_spec.validator_id,
            severity="blocker",
            justification=(
                f"Validator tool-loop reached the maximum of {tool_turns} "
                "turns without calling submit_verdict."
            ),
            error=True,
        )

    def _build_tool_loop_prompt(
        self,
        context: ValidatorContext,
        active_names: Set[str],
        has_diff: bool,
        change_diff: str,
        diff_label: str = "",
        diff_is_fallback: bool = False,
    ) -> str:
        """Build the tool-loop judge prompt for this validator.

        Subclasses override this to change what the judge is asked to
        evaluate (e.g. the acceptance validator judges the produced
        artifacts against the task's acceptance criteria instead of
        protocol criteria).
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
        ]

        if has_diff and change_diff:
            label = diff_label or "HEAD~1..HEAD"
            parts = [
                "\n",
                f"## Code Change ({label})\n",
                f"```\n{change_diff}\n```\n",
            ]
            if diff_is_fallback:
                parts.append(
                    "NOTE: this diff was read against HEAD~1..HEAD because no "
                    "execute-node HEAD anchor was available — it may show the "
                    "previous commit rather than this task's produced change.\n"
                )
            prompt_parts.extend(parts)

        prompt_parts.extend([
            "\n",
            "## Available Tools\n",
            "You may call read-only tools to inspect files and git history.\n",
            "When you are ready to deliver your verdict, call the\n",
            "`submit_verdict(severity, justification)` tool — this is the\n",
            "ONLY way to return your verdict.  Do NOT narrate your verdict\n",
            "as prose; use the tool.\n",
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
            except (json.JSONDecodeError, TypeError):
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
        else:
            task = context_or_task.task
            phase = getattr(context_or_task, "phase", "") or ""
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
        retrying it is not honest).
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self._completion_fn(**kwargs)
            except Exception as e:
                is_transient = _is_transient_error(e)
                if is_transient and attempt < max_retries - 1:
                    _logger.warning(
                        "Transient LLM provider error on attempt %d for validator %s: %s; retrying...",
                        attempt + 1,
                        self.validator_spec.validator_id,
                        e,
                    )
                    time.sleep(0.05 * (2 ** attempt))
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
        except (json.JSONDecodeError, ValueError):
            return None

    def _try_extract_json(self, text: str) -> Optional[dict]:
        """Try to extract JSON from text with surrounding content."""
        # Try code block extraction
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # Try finding JSON object in text
        match = re.search(r'\{[^{}]*"severity"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

        return None


def _truncated_log(raw: str, max_chars: int = 2048) -> str:
    """Truncate a raw response string for logging."""
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "...<truncated>"


_default_registry.register_compound(LLMValidator.HANDLED_TYPES, LLMValidator)
