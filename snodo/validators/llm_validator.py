"""LLM Validator - AI-driven pre-execute validation.

FILE: snodo/validators/llm_validator.py (Task 6.1)

Uses the existing Coder adapter's LLM to evaluate tasks against
protocol-defined criteria before execution.

Judge prompt contract:
- Input: task spec + validator criteria from protocol YAML
- Output: JSON with {severity, justification}
- Falls back to "warn" on any LLM or parse failure

Post-execute tool loop (added):
- For post_execute phase with MCPs available, runs a bounded
  read-only tool-use loop so the validator can inspect the actual
  change (diff HEAD~1..HEAD) and read files on demand.
"""

import json
import re
from typing import Any, Dict, List, Optional

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import _default_registry


# Maximum tool-use turns before forcing a verdict.
_MAX_TOOL_TURNS = 6


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
        model: str = "gpt-4",
    ):
        self.validator_spec = validator_spec
        self._completion_fn = completion_fn
        self.model = model

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

        # Phase gating: post-execute with MCPs → tool-loop path
        if (
            getattr(context, "phase", "") == "post_execute"
            and context.workspace_mcp is not None
            and context.git_mcp is not None
            and self._completion_fn is not None
        ):
            return self._evaluate_with_tools(context)

        # Pre-execute or fallback: single-completion path (unchanged)
        prompt = self._build_prompt(context)

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
        """Run a bounded read-only tool-use loop for post-execute validation.

        Prepends the HEAD~1..HEAD diff into the first prompt so the
        common case needs no tool calls.  Exposes a read-only toolset
        via completion_fn(tools=[...]).  Bounded to _MAX_TOOL_TURNS.
        """
        workspace = context.workspace_mcp
        git = context.git_mcp

        # Gather the "what changed" diff upfront
        try:
            change_diff = git.diff_between_refs("HEAD~1", "HEAD")
        except Exception:
            change_diff = "(unable to read diff HEAD~1..HEAD)"

        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )

        system_prompt = (
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n"
            f"Evaluate the ACTUAL CODE CHANGE against the criteria below.\n"
            f"\n"
            f"## Task\n"
            f"{context.task.spec}\n"
            f"\n"
            f"## Criteria\n"
            f"{criteria_text}\n"
            f"\n"
            f"## Code Change (HEAD~1..HEAD)\n"
            f"```\n{change_diff}\n```\n"
            f"\n"
            f"## Available Tools\n"
            f"You may call read-only tools to inspect files and git history.\n"
            f"Use them if you need more context beyond the diff above.\n"
            f"\n"
            f"## Instructions\n"
            f"Evaluate the code change against EACH criterion.\n"
            f"Use tools to read files if needed, then return your verdict.\n"
            f"Return your FINAL verdict as a JSON object with exactly two fields:\n"
            f"- \"severity\": one of \"pass\", \"warn\", or \"blocker\"\n"
            f"- \"justification\": a brief explanation of your evaluation\n"
            f"\n"
            f"Respond with ONLY the JSON object for your final verdict, no other text.\n"
            f"\n"
            f'Example:\n'
            f'{{"severity": "pass", "justification": "Change meets all criteria."}}\n'
        )

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": system_prompt},
        ]

        tools = self._build_tool_definitions()

        for turn in range(_MAX_TOOL_TURNS):
            try:
                response = self._completion_fn(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=0.0,
                    max_tokens=500,
                )
            except Exception as e:
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="warn",
                    justification=f"LLM tool-loop error on turn {turn + 1}: {e}",
                )

            msg = response.choices[0].message

            # Check if the model returned a final text response (verdict)
            if msg.content is not None and not getattr(msg, "tool_calls", None):
                return self._parse_response(msg.content)

            # Execute tool calls
            tool_calls = getattr(msg, "tool_calls", [])
            if not tool_calls:
                # No content and no tool calls — force verdict
                return ValidatorResult(
                    validator_id=self.validator_spec.validator_id,
                    severity="warn",
                    justification=(
                        "LLM returned neither a verdict nor tool calls "
                        f"on turn {turn + 1}. Diff was provided but no "
                        "judgment could be parsed."
                    ),
                )

            # Add assistant message with tool_calls to conversation
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

            # Execute each tool call and append results
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}

                result = self._execute_tool(tool_name, args, workspace, git)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })

        # Hit the turn cap — force a verdict from what we have
        return ValidatorResult(
            validator_id=self.validator_spec.validator_id,
            severity="warn",
            justification=(
                f"Validator tool-loop reached the maximum of {_MAX_TOOL_TURNS} "
                f"turns without a final verdict. Partial inspection was performed."
            ),
        )

    @staticmethod
    def _build_tool_definitions() -> List[Dict[str, Any]]:
        """Build OpenAI-format tool definitions for the read-only toolset."""
        return [
            {
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
            {
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
            {
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
            {
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
            {
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
            {
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
        ]

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
        response = self._completion_fn(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=500,
        )
        return response.choices[0].message.content

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


_default_registry.register_compound(LLMValidator.HANDLED_TYPES, LLMValidator)
