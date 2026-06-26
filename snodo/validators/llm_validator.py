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
- Phase only filters read_diff_between_refs (meaningful post-execute only).
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

from litellm import supports_response_schema

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import _default_registry
from snodo.infrastructure.config import DEFAULT_MODEL

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


def _is_gemini3_plus(model: str) -> bool:
    m = re.search(r'gemini-(\d+)', model)
    return bool(m and int(m.group(1)) >= 3)


class LLMValidator(ValidatorBase):
    """Evaluates tasks against protocol criteria using an LLM judge."""

    VALID_SEVERITIES = {"pass", "warn", "blocker", "error"}

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

    def _resolve_cf_headers(self) -> Optional[dict]:
        """Return extra_headers for Cloudflare Workers AI session affinity."""
        from snodo.config import ConfigManager
        provider = ConfigManager._provider_for_model(self.model)
        if provider == "cloudflare":
            return {"x-session-affinity": self._task_id or "unknown"}
        return None

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
        declared_tools = getattr(self.validator_spec, "tools", None) or []
        if (
            declared_tools
            and context.workspace_mcp is not None
            and context.git_mcp is not None
            and self._completion_fn is not None
        ):
            return self._evaluate_with_tools(context)

        # Pre-execute or fallback: single-completion path
        prompt = self._build_prompt(context)

        # Try structured output when the model supports it
        if self._completion_fn is not None and supports_response_schema(self.model):
            try:
                return self._call_llm_structured(prompt)
            except Exception:
                pass  # fall through to legacy parse

        # Legacy: free-text completion + hand-rolled parse
        if self._completion_fn is None:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification="No completion_fn available",
            )
        try:
            response_text = self._call_llm(prompt)
            return self._parse_response(response_text)
        except Exception as e:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"LLM validation failed, defaulting to warn: {e}",
            )

    # ------------------------------------------------------------------
    # Post-execute bounded tool-use loop
    # ------------------------------------------------------------------

    def _evaluate_with_tools(self, context: ValidatorContext) -> ValidatorResult:
        """Run a bounded read-only tool-use loop.

        Activated by declared tools on the validator spec (not phase).
        Phase only filters read_diff_between_refs (meaningful post-execute).
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

        # Only prepend diff when the diff tool is in the active set
        has_diff = "read_diff_between_refs" in active_names
        change_diff = ""
        if has_diff:
            try:
                change_diff = git.diff_between_refs("HEAD~1", "HEAD")
            except Exception:
                change_diff = "(unable to read diff HEAD~1..HEAD)"

        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )

        prompt_parts = [
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n",
            "Evaluate the task against the criteria below.\n",
            "\n",
            "## Task\n",
            f"{context.task.spec}\n",
            "\n",
            "## Criteria\n",
            f"{criteria_text}\n",
        ]

        if has_diff and change_diff:
            prompt_parts.extend([
                "\n",
                "## Code Change (HEAD~1..HEAD)\n",
                f"```\n{change_diff}\n```\n",
            ])

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

        system_prompt = "".join(prompt_parts)

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": system_prompt},
        ]

        retried_free_text = False

        for turn in range(tool_turns):
            try:
                kwargs = {
                    "model": self.model,
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
                cf_headers = self._resolve_cf_headers()
                if cf_headers:
                    kwargs["extra_headers"] = cf_headers
                response = self._completion_fn(**kwargs)
            except Exception as e:
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="warn",
                    justification=f"LLM tool-loop error on turn {turn + 1}: {e}",
                )

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", [])

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
                        continue  # already handled above

                    result = self._execute_tool(tool_name, args, workspace, git)

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
                severity="error",
                justification=(
                    f"Validator did not call submit_verdict after {turn + 1} turn(s). "
                    "No reliable verdict could be obtained."
                ),
            )

        # Hit the turn cap — fail closed
        return ValidatorResult(
            validator_id=self.validator_spec.validator_id,
            severity="error",
            justification=(
                f"Validator tool-loop reached the maximum of {tool_turns} "
                "turns without calling submit_verdict."
            ),
        )

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
        else:
            task = context_or_task.task
        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )

        return (
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n"
            f"Evaluate the following task against the criteria below.\n"
            f"\n"
            f"## Task\n"
            f"{task.spec}\n"
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

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM via the completion function.

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
        response = self._completion_fn(**kwargs)
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
        response = self._completion_fn(**kwargs)
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
