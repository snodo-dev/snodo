"""LiteLLM coder adapter.

FILE: snodo/coders/litellm.py

Implements CoderAdapter using LangChain + liteLLM for model abstraction.

Bounded tool-use loop (added):
- When workspace_mcp is available, _call_llm runs a bounded read-only
  tool-use loop over completion_fn(tools=[...]) so the coder can read
  current file contents before generating a CodeArtifact.
- Read-only tools: read_file, read_file_lines, list_files.
- NO write tool, NO shell. The coder still returns a CodeArtifact;
  the executor owns writes.
- Bounded to _MAX_TOOL_TURNS turns. When no read is needed, the model
  returns the CodeArtifact on the first turn (behaviour-equivalent to
  the old single-completion path).
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact, MCPServer
from snodo.coders.base import CoderAdapter, LLMCallError, ParseError, TurnBudgetExhausted
from snodo.engine.progress import format_elapsed, format_tool_call_summary
from snodo.infrastructure.config import DEFAULT_MODEL
from snodo.infrastructure.usage_tracker import UsageTracker

import litellm as _litellm
_litellm.drop_params = True
_litellm.suppress_debug_info = True

# Retry transient errors (5xx, 429, connection) with exponential backoff
try:
    from snodo.infrastructure.config import load_llm_config
    _litellm.num_retries = load_llm_config().num_retries
except Exception:
    _litellm.num_retries = 3

if not getattr(_litellm, "success_callback", None):
    _litellm.success_callback = []
_litellm.success_callback.append(UsageTracker())

import logging as _logging  # noqa: E402  — must run after litellm is configured above

# CF models absent from models.dev catalog — price via register_model. The
# register_model call emits a WARNING per model ("not in built-in cost map")
# because the entries lack cache cost fields. These fire on every import —
# including `snodo --version` — and publish the model catalog into the
# terminal. Suppress the LiteLLM logger for the duration of the call only,
# then restore the previous level: a real cost or routing warning during a
# run must still surface (Fixes #135).
_litellm_logger = _logging.getLogger("LiteLLM")
_prev_litellm_level = _litellm_logger.level
_litellm_logger.setLevel(_logging.CRITICAL)
try:
    _litellm.register_model({
        "openai/@cf/google/gemma-4-26b-a4b-it": {
            "input_cost_per_token": 0.10 / 1_000_000,
            "output_cost_per_token": 0.30 / 1_000_000,
        },
        "openai/@cf/nvidia/nemotron-3-120b-a12b": {
            "input_cost_per_token": 0.50 / 1_000_000,
            "output_cost_per_token": 1.50 / 1_000_000,
        },
        "openai/@cf/moonshotai/kimi-k2.6": {
            "input_cost_per_token": 0.95 / 1_000_000,
            "output_cost_per_token": 4.00 / 1_000_000,
        },
        "openai/@cf/moonshotai/kimi-k2.7-code": {
            "input_cost_per_token": 0.95 / 1_000_000,
            "output_cost_per_token": 4.00 / 1_000_000,
        },
        "openai/@cf/mistralai/mistral-small-3.1-24b-instruct": {
            "input_cost_per_token": 0.35 / 1_000_000,
            "output_cost_per_token": 0.55 / 1_000_000,
        },
    })
finally:
    _litellm_logger.setLevel(_prev_litellm_level)

_logger = logging.getLogger(__name__)


# Maximum tool-use turns before forcing a CodeArtifact parse.
_DEFAULT_MAX_TOOL_TURNS = 20


def _is_gemini3_plus(model: str) -> bool:
    """Check if model is Gemini 3.0 or higher."""
    return "gemini-3" in model.lower() or "gemini-4" in model.lower()


def _is_test_governing_file(path: str) -> bool:
    """Check if a file path governs test behavior (ADR 040 Point 5)."""
    p = str(path).replace("\\", "/").strip().lower()
    parts = p.split("/")
    filename = parts[-1]

    if any(part in {"tests", "test", "spec", "specs"} for part in parts[:-1]):
        return True

    if (
        filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".test.js")
        or filename.endswith(".test.ts")
        or filename.endswith(".spec.js")
        or filename.endswith(".spec.ts")
    ):
        return True

    governing_filenames = {
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        "pyproject.toml",
        ".coveragerc",
        "setup.cfg",
        "cargo.toml",
        "package.json",
        "jest.config.js",
        "jest.config.ts",
        "vitest.config.ts",
    }
    return filename in governing_filenames


class LiteLLMAdapter(CoderAdapter):
    """Base coder adapter using liteLLM as transport.

    Provider-agnostic tool-use loop. Subclasses override only
    TRUNCATION_REASONS and (optionally) _call_llm_with_tools for
    provider-specific message shaping.
    """

    TRUNCATION_REASONS: set[str] = {"length", "max_tokens", "MAX_TOKENS"}

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        mcp_servers: Optional[List[MCPServer]] = None,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        max_tool_turns: Optional[int] = None,
        workspace_mcp: Optional[Any] = None,
        progress_callback: Optional[Any] = None,
        **kwargs: Any,
    ):
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tool_turns = max_tool_turns if max_tool_turns is not None else _DEFAULT_MAX_TOOL_TURNS
        self.workspace_mcp = workspace_mcp
        self.progress_callback = progress_callback
        self.observes_tests = True

        self._job_id: str = ""
        self._task_id: str = ""
        #: Per-attempt read memory; None until a tool-loop run populates it.
        #: Exposed as paths only (see ``last_read_paths``) so a later recovery
        #: attempt can reuse the map of what was inspected, never its contents.
        self._read_tracker: Optional["ReadMemoryTracker"] = None

        try:
            from litellm import completion
            self._completion_fn = completion
        except ImportError:
            self._completion_fn = None

    @property
    def last_read_paths(self) -> List[str]:
        """File paths opened by the most recent attempt (paths only, no contents)."""
        return list(self._read_tracker.file_ranges) if self._read_tracker else []

    @property
    def last_listed_dirs(self) -> List[str]:
        """Directory paths listed by the most recent attempt."""
        return list(self._read_tracker.dir_listings) if self._read_tracker else []

    def _resolve_api_base(self) -> Optional[str]:
        """Return api_base for the current model, if provider has base_url set."""
        from snodo.config import ConfigManager
        return ConfigManager.resolve_api_base(self.model)

    def _resolve_extra_headers(self) -> Optional[dict]:
        """Return extra_headers for the model's provider."""
        from snodo.config import ConfigManager
        return ConfigManager.resolve_extra_headers(self.model, task_id=self._task_id)

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        self._read_tracker = None
        prompt = self._build_prompt(spec)
        response = self._call_llm(prompt)
        return self._parse_response(response)

    def _build_prompt(self, spec: TaskSpec) -> str:
        language = spec.project_context.get("language", "unknown")
        lang_hint = f" ({language} project)" if language != "unknown" else ""

        prompt_parts = [
            f"You are an expert software engineer{lang_hint}. "
            "Generate code based on this specification:\n",
        ]

        # Project context section
        structure = spec.project_context.get("structure", "")
        config_files = spec.project_context.get("config_files", {})
        if structure or config_files:
            prompt_parts.append("\n## Project Context\n")
            if structure:
                prompt_parts.append(f"Directory structure:\n```\n{structure}\n```\n")
            for cfg_name, cfg_content in config_files.items():
                prompt_parts.append(f"{cfg_name}:\n```\n{cfg_content}\n```\n")

        # Task section
        prompt_parts.append(f"\n## Task\nDescription: {spec.description}\n")

        if spec.constraints:
            prompt_parts.append("\nConstraints:")
            for constraint in spec.constraints:
                prompt_parts.append(f"- {constraint}")
            prompt_parts.append("\n")

        # Tool hint (when workspace available)
        if self.workspace_mcp is not None:
            prompt_parts.append(
                "\n## Available Tools\n"
                "Tool calls and turns are expensive! Prefer reading multiple files in a single turn using "
                "`read_files(paths=[...])` rather than making individual `read_file` calls. You may also use "
                "`read_file(path)` for a single file, `read_file_lines(path, start, end)` for line ranges, and "
                "`list_files(directory)` to explore the project.\n"
                "You can search inside file contents using `search_string(query, directory)` — `query` is the text to FIND, "
                "and the folder to look in goes in `directory` — and locate symbol definitions using "
                "`search_symbol(name, directory)` — `name` is an identifier like \"AuthManager\", never a path.\n"
                "You can run the project's declared test runner to observe test output before submitting using `run_tests(test_path, command_type)`.\n"
                "Read existing files you need to modify so you can make faithful edits.\n"
                "\n"
                "When you are ready to deliver your changes, call the `submit_files(files)` tool. "
                "You may call `submit_files` multiple times across turns to stage or update files. "
                "File operations accumulate by path across calls and are delivered atomically when complete. "
                "Do NOT emit file content as prose or as a JSON text blob.\n"
                "To remove obsolete or orphaned files created in earlier attempts, include a file item with action: \"delete\" (content is optional for deletes).\n"
                "\n"
            )

        prompt_parts.append("""
## Output Format
Your response MUST be a JSON array of file operations. Each element has:
- "path": file path relative to the project root
- "content": the full file content (required for "write", optional for "delete")
- "action": "write" (default) or "delete" (use to remove obsolete or orphaned files)

Return ONLY the JSON array, no other text.

```json
[
  {"path": "src/module.py", "content": "def my_function():\\n    pass\\n", "action": "write"},
  {"path": "tests/test_module.py", "content": "def test_my_function():\\n    assert my_function() is not None\\n", "action": "write"},
  {"path": "src/old_orphan.py", "action": "delete"}
]
```
""")

        if spec.memory_summary:
            prompt_parts.append(f"\n## Session History\n{spec.memory_summary}\n")

        prompt_parts.append("Now generate the implementation:\n")

        return "".join(prompt_parts)

    def _call_llm(self, prompt: str) -> str:
        if self._completion_fn is None:
            raise LLMCallError(
                "litellm not available. Install with: pip install litellm"
            )

        # When workspace_mcp is available, use bounded tool-use loop
        if self.workspace_mcp is not None:
            return self._call_llm_with_tools(prompt)

        # Fallback: single raw completion (backward-compatible)
        try:
            from snodo.config import ConfigManager
            kwargs = {
                "model": ConfigManager.resolve_litellm_model(self.model),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.max_tokens,
                "metadata": {
                    "job_id": self._job_id or "unknown",
                    "task_id": self._task_id or "unknown",
                    "role": "coder",
                },
            }
            api_base = self._resolve_api_base()
            if api_base:
                kwargs["api_base"] = api_base
            extra_headers = self._resolve_extra_headers()
            if extra_headers:
                kwargs["extra_headers"] = extra_headers
            if not _is_gemini3_plus(self.model):
                kwargs["temperature"] = self.temperature
            response = self._completion_fn(**kwargs)
            self._check_truncation(response)
            return response.choices[0].message.content
        except (LLMCallError, ParseError):
            raise
        except Exception as e:
            raise LLMCallError(f"LLM call failed: {e}") from e

    def _finalize_accumulated_deliveries(
        self,
        accumulated_files: Dict[str, Dict[str, Any]],
        test_governing_mutations: Dict[str, Dict[str, str]],
    ) -> str:
        """Finalize accumulated deliveries, record audit events, and attach metadata."""
        metadata: Dict[str, Any] = {}
        if test_governing_mutations:
            mutations_list = list(test_governing_mutations.values())
            metadata["test_governing_mutations"] = mutations_list
            metadata["test_mutation_detected"] = True
            self._emit_audit_event(
                "test_modified",
                {
                    "mutations": mutations_list,
                    "job_id": self._job_id,
                    "task_id": self._task_id,
                },
            )
        self._last_artifact_metadata = metadata
        return json.dumps(list(accumulated_files.values()))

    def _call_llm_with_tools(self, prompt: str) -> str:
        """Bounded tool-use loop with submit_files, search tools, and test runner observation."""
        workspace = self.workspace_mcp
        tools = self._build_tool_definitions()
        tools.append(self._SUBMIT_FILES_DEF)

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": prompt},
        ]

        retried_free_text = False
        finish_reason = None
        start_time = time.monotonic()
        read_tracker = ReadMemoryTracker(getattr(workspace, "project_root", None))
        accumulated_files: Dict[str, Dict[str, Any]] = {}
        test_governing_mutations: Dict[str, Dict[str, str]] = {}
        self._read_tracker = read_tracker

        for turn in range(self.max_tool_turns):
            turn_start = time.monotonic()
            test_runs_this_turn = 0
            try:
                from snodo.config import ConfigManager
                kwargs = {
                    "model": ConfigManager.resolve_litellm_model(self.model),
                    "messages": messages,
                    "tools": tools,
                    "parallel_tool_calls": True,
                    "max_tokens": self.max_tokens,
                    "metadata": {
                        "job_id": self._job_id or "unknown",
                        "task_id": self._task_id or "unknown",
                        "role": "coder",
                    },
                }
                api_base = self._resolve_api_base()
                if api_base:
                    kwargs["api_base"] = api_base
                extra_headers = self._resolve_extra_headers()
                if extra_headers:
                    kwargs["extra_headers"] = extra_headers
                if not _is_gemini3_plus(self.model):
                    kwargs["temperature"] = self.temperature
                response = self._completion_fn(**kwargs)
            except Exception as e:
                self._emit_turn_telemetry(
                    turn_index=turn + 1,
                    tool="error",
                    target_path="",
                    read_hit=False,
                    tokens_in=0,
                    tokens_out=0,
                    elapsed_ms=(time.monotonic() - turn_start) * 1000,
                )
                staged_info = f" ({len(accumulated_files)} file(s) staged)" if accumulated_files else ""
                raise LLMCallError(
                    f"LLM tool-loop error on turn {turn + 1}{staged_info}: {e}"
                ) from e

            self._check_truncation(response)

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", [])
            finish_reason = getattr(response.choices[0], "finish_reason", None)

            if self.progress_callback:
                elapsed_str = format_elapsed(time.monotonic() - start_time)
                tools_str = format_tool_call_summary(tool_calls)
                self.progress_callback(f"    [{elapsed_str}] Turn {turn + 1}: {tools_str}")

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
                    if tool_name == "submit_files":
                        files_list = self._extract_submit_files(tc)
                        if files_list is not None and files_list:
                            for f in files_list:
                                if isinstance(f, dict) and "path" in f:
                                    path = f["path"]
                                    action = f.get("action", "write")
                                    accumulated_files[path] = f
                                    if _is_test_governing_file(path):
                                        test_governing_mutations[path] = {
                                            "path": path,
                                            "kind": action,
                                        }
                            self._emit_turn_telemetry(
                                turn_index=turn + 1,
                                tool="submit_files",
                                target_path="",
                                read_hit=False,
                                tokens_in=_usage_tokens(response, "prompt"),
                                tokens_out=_usage_tokens(response, "completion"),
                                elapsed_ms=(time.monotonic() - turn_start) * 1000,
                                submit_bytes=len(json.dumps(files_list).encode("utf-8")),
                            )
                            staged_count = len(files_list)
                            total_count = len(accumulated_files)
                            result = (
                                f"Staged {staged_count} file operation(s). Total accumulated across turns: {total_count} file(s). "
                                f"You may call submit_files again to stage additional or updated files, or finish when complete."
                            )
                        else:
                            result = self._submit_files_feedback(tc)
                    elif tool_name == "run_tests":
                        if test_runs_this_turn >= 3:
                            result = "Test execution limit reached for this turn (max 3 runs per turn). Proceed with implementation or submit_files."
                        else:
                            test_runs_this_turn += 1
                            try:
                                args = json.loads(tc.function.arguments)
                            except (json.JSONDecodeError, TypeError):
                                args = {}
                            test_path = args.get("test_path", "")
                            command_type = args.get("command_type", "pytest")
                            result = self._execute_coder_run_tests(test_path, command_type, workspace, turn + 1)
                        self._emit_turn_telemetry(
                            turn_index=turn + 1,
                            tool="run_tests",
                            target_path=_normalize_path_arg(args if 'args' in locals() else {}),
                            read_hit=False,
                            tokens_in=_usage_tokens(response, "prompt"),
                            tokens_out=_usage_tokens(response, "completion"),
                            elapsed_ms=(time.monotonic() - turn_start) * 1000,
                        )
                    else:
                        try:
                            args = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        prev_turn = read_tracker.check_read(tool_name, args)
                        if prev_turn is not None:
                            result = format_repeat_read_response(tool_name, args, prev_turn)
                        else:
                            result = self._execute_tool(tool_name, args, workspace)
                            read_tracker.record_read(tool_name, args, turn + 1)
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

            # No tool calls on this turn — deliver accumulated files if any
            if accumulated_files:
                return self._finalize_accumulated_deliveries(accumulated_files, test_governing_mutations)

            # No tool calls — free-text, try corrective retry once
            if msg.content is not None and not retried_free_text:
                retried_free_text = True
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                })
                messages.append({
                    "role": "user",
                    "content": (
                        "Deliver your changes by calling "
                        "submit_files(files=[...]). Do not "
                        "emit them as text."
                    ),
                })
                continue

            # Fallback: try to parse free-text as file operations
            if msg.content is not None:
                return self._try_parse_or_fail(
                    msg.content, turn, finish_reason,
                )
            break
        else:
            # The loop completed every allowed turn without returning: the
            # coder exhausted its turn budget. This is a bounded, anticipated
            # outcome with its own identity — never a crash, never a parse
            # failure — so it must not be laundered into ``internal_error``.
            if accumulated_files:
                return self._finalize_accumulated_deliveries(
                    accumulated_files, test_governing_mutations,
                )
            self._raise_turn_budget_exhausted(finish_reason)

        # Early break (a content-less response with no tool calls), not turn
        # exhaustion. Try the last assistant content for a legacy free-text
        # delivery, then fail loudly.
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return self._try_parse_or_fail(
                    m["content"], self.max_tool_turns - 1, finish_reason,
                )

        # No content at all — diagnostic failure
        self._raise_tool_loop_failure(
            "", self.max_tool_turns, finish_reason,
        )

    def _try_parse_or_fail(
        self, content: str, turns_used: int, finish_reason: Optional[str],
    ) -> str:
        """Attempt to parse content as file operations; raise diagnostic if not."""
        parsed = self._extract_json(content)
        if parsed is not None and isinstance(parsed, list):
            # Validate it looks like file operations
            if all(isinstance(item, dict) for item in parsed):
                return content
        self._raise_tool_loop_failure(content, turns_used, finish_reason)

    def _raise_tool_loop_failure(
        self, content: str, turns_used: int, finish_reason: Optional[str],
    ) -> None:
        """Raise a diagnostic ParseError when the tool loop ends without files."""
        preview = content[:200] if content else "(empty)"
        raise ParseError(
            "Coder completed the tool loop without delivering files via "
            "submit_files. Final response was empty or unparseable. "
            f"Model: {self.model}, turns used: {turns_used + 1}, "
            f"finish_reason: {finish_reason}, "
            f"content preview: {preview}"
        )

    def _raise_turn_budget_exhausted(self, finish_reason: Optional[str]) -> None:
        """Raise the distinct turn-budget outcome when the coder runs out of turns.

        The coder used every allowed tool turn without calling ``submit_files``.
        This is not a crash and not an unparseable response; it is a bounded,
        anticipated outcome the engine reports under its own halt outcome.
        """
        raise TurnBudgetExhausted(
            "Coder exhausted its turn budget "
            f"({self.max_tool_turns} turns) without submitting files via "
            "submit_files. Model: "
            f"{self.model}, finish_reason: {finish_reason}."
        )

    _SUBMIT_FILES_DEF = {
        "type": "function",
        "function": {
            "name": "submit_files",
            "description": (
                "Submit file operations. You may call this tool multiple times across turns "
                "to stage or update files. File operations accumulate by path across calls and "
                "are applied atomically when complete. Each file has path, optional content "
                "(required for write, optional for delete), and an optional action (\"write\" or \"delete\"). "
                "Use action=\"delete\" to remove obsolete or orphaned files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "File path relative to project root",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Full file content (required for write, optional for delete)",
                                },
                                "action": {
                                    "type": "string",
                                    "enum": ["write", "delete"],
                                    "description": "write (default) or delete. Use delete to remove orphaned or obsolete files.",
                                },
                            },
                            "required": ["path"],
                        },
                        "description": "Array of file operations",
                    },
                },
                "required": ["files"],
            },
        },
    }

    @staticmethod
    def _extract_submit_files(target: Any) -> Optional[List[Dict]]:
        """Extract files list from a submit_files tool call object or tool_calls list.

        Returns None if submit_files is not present or has invalid/unparseable arguments.
        """
        if target is None:
            return None
        if isinstance(target, list):
            for tc in target:
                res = LiteLLMAdapter._extract_submit_files(tc)
                if res is not None:
                    return res
            return None

        name = getattr(getattr(target, "function", None), "name", None)
        if name != "submit_files":
            return None

        try:
            raw_args = getattr(target.function, "arguments", "{}")
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except (json.JSONDecodeError, TypeError):
            return None

        files = args.get("files", [])
        if isinstance(files, list):
            return files
        return None

    @staticmethod
    def _submit_files_feedback(tc: Any) -> str:
        """Build a tool response for a submit_files call that is not a valid
        completion (zero files or unparseable arguments).

        The response tells the model to actually produce files; it is appended
        as a tool message so the tool_call_id is always answered before the
        next request.
        """
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}
        files = args.get("files", [])
        if isinstance(files, list) and not files:
            return (
                "submit_files was called with an empty files list. "
                "You must deliver at least one file operation. "
                "Produce the required files and call submit_files(files=[...]) "
                "with the actual file operations."
            )
        return (
            "submit_files arguments were invalid or unparseable. "
            "Call submit_files(files=[...]) with a valid JSON array of "
            "file operations."
        )

    def _check_truncation(self, response: Any) -> None:
        """Raise ParseError if the completion was truncated at max_tokens."""
        try:
            choice = response.choices[0]
            finish = getattr(choice, "finish_reason", None)
            if finish in self.TRUNCATION_REASONS:
                raw = str(getattr(choice.message, "content", "") or "")
                usage = getattr(response, "usage", None)
                tokens = None
                if usage:
                    c_tok = getattr(usage, "completion_tokens", None)
                    o_tok = getattr(usage, "output_tokens", None)
                    if isinstance(c_tok, int):
                        tokens = c_tok
                    elif isinstance(o_tok, int):
                        tokens = o_tok

                gen_info = f"{tokens} tokens ({len(raw)} chars)" if tokens is not None else f"{len(raw)} chars"

                _logger.warning(
                    "Coder output truncated at max_tokens=%s — "
                    "generated %s before truncation — raw response (first 2KB): %s",
                    self.max_tokens,
                    gen_info,
                    _truncated_log(raw),
                )
                # Report what was observed; label inference as inference
                # (Fixes #67).  The observed facts are the finish_reason and
                # the generated token/char counts.  "The task is too large" is
                # an inference, not an observation: the same finish_reason
                # occurs when a tool call's arguments exceed the output budget
                # and are cut off mid-argument — a different fault with a
                # different remedy.
                raise ParseError(
                    f"Coder output stopped at max_tokens={self.max_tokens} "
                    f"(finish_reason={finish}); generated {gen_info}. "
                    f"Observed: the response was cut off at the token ceiling. "
                    f"Inference (not confirmed): the task is too large, or a "
                    f"tool call's arguments exceeded the output budget and "
                    f"were cut off mid-argument. Raise max_tokens or split "
                    f"the task."
                )
        except (AttributeError, IndexError):
            pass

    @staticmethod
    def _build_tool_definitions() -> List[Dict[str, Any]]:
        """Build OpenAI-format tool definitions for the read-only toolset."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_files",
                    "description": "Read full contents of multiple files in a single tool call (turns are expensive, use this to read multiple files at once)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "File paths relative to project root",
                            },
                        },
                        "required": ["paths"],
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
            {
                "type": "function",
                "function": {
                    "name": "search_string",
                    "description": (
                        "Search inside file contents for a literal string. `query` is the TEXT "
                        "to find — it is never a file or directory path. To restrict the search "
                        "to a folder, pass that folder as `directory`. To see what a folder "
                        "contains, use list_files instead."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Literal text to find inside file contents. NOT a path — file and directory paths belong in `directory`.",
                            },
                            "directory": {
                                "type": "string",
                                "description": "Optional folder to search inside, relative to project root (e.g. \"src\"). Defaults to \".\" (the whole project).",
                                "default": ".",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_symbol",
                    "description": (
                        "Find where a symbol is defined (class, def, function, struct, fn, type, "
                        "const). `name` is an identifier such as \"AuthManager\" — it is never a "
                        "file or directory path. To restrict the search to a folder, pass that "
                        "folder as `directory`."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Symbol identifier to locate (e.g. \"parse_config\"). NOT a path — file and directory paths belong in `directory`.",
                            },
                            "directory": {
                                "type": "string",
                                "description": "Optional folder to search inside, relative to project root (e.g. \"src\"). Defaults to \".\" (the whole project).",
                                "default": ".",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "Run the project's declared test runner (pytest, npm, cargo) to observe test output before submitting edits",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "test_path": {"type": "string", "description": "Path to test file or directory (relative to project root)", "default": ""},
                            "command_type": {"type": "string", "enum": ["pytest", "npm", "cargo"], "default": "pytest", "description": "Test command type"},
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
    ) -> str:
        """Execute a read-only tool call and return the result as a string."""
        try:
            if name == "read_files":
                paths = args.get("paths", [])
                if not isinstance(paths, list) or not paths:
                    return "No paths provided to read_files."

                max_paths = 10
                max_bytes = 32768

                requested_paths = [str(p) for p in paths]
                processed_paths = requested_paths[:max_paths]
                omitted_paths = requested_paths[max_paths:]

                results = []
                current_bytes = 0
                truncated = False

                for idx, p in enumerate(processed_paths):
                    try:
                        content = workspace.read_file(p)
                        header = f"=== {p} ===\n"
                        entry = header + content
                        entry_bytes = len(entry.encode("utf-8"))

                        if current_bytes + entry_bytes > max_bytes:
                            allowed_content_bytes = max_bytes - current_bytes - len(header.encode("utf-8")) - 100
                            if allowed_content_bytes > 0:
                                truncated_content = content.encode("utf-8")[:allowed_content_bytes].decode("utf-8", errors="ignore")
                                results.append(f"{header}{truncated_content}\n[TRUNCATED: Content cut off at 32KB batch limit]")
                            else:
                                results.append(f"{header}[OMITTED: Exceeded 32KB batch limit]")
                            truncated = True
                            omitted_paths.extend(processed_paths[idx + 1:])
                            break
                        else:
                            results.append(entry)
                            current_bytes += entry_bytes
                    except Exception as e:
                        results.append(f"=== {p} ===\nError reading file: {e}")

                output = "\n\n".join(results)
                if omitted_paths or truncated:
                    notice_parts = []
                    if omitted_paths:
                        notice_parts.append(f"{len(omitted_paths)} path(s) omitted ({', '.join(omitted_paths)})")
                    if truncated:
                        notice_parts.append("content truncated at 32KB batch limit")
                    output += f"\n\n[TRUNCATED BATCH: {'; '.join(notice_parts)}. Use read_file_lines(path, start, end) for targeted line ranges.]"
                return output
            elif name == "read_file":
                return workspace.read_file(args["path"])
            elif name == "read_file_lines":
                return workspace.read_file_lines(args["path"], args["start"], args["end"])
            elif name == "list_files":
                return "\n".join(workspace.list_files(args.get("directory", ".")))
            elif name == "search_string":
                query = args.get("query", "")
                directory = args.get("directory", ".")
                misuse = _search_term_directory_guidance(workspace, "search_string", "query", query, directory)
                if misuse is not None:
                    return misuse
                if hasattr(workspace, "search_string"):
                    return workspace.search_string(query, directory)
                return f"search_string unavailable on workspace: {workspace}"
            elif name == "search_symbol":
                symbol_name = args.get("name", "")
                directory = args.get("directory", ".")
                misuse = _search_term_directory_guidance(workspace, "search_symbol", "name", symbol_name, directory)
                if misuse is not None:
                    return misuse
                if hasattr(workspace, "search_symbol"):
                    return workspace.search_symbol(symbol_name, directory)
                return f"search_symbol unavailable on workspace: {workspace}"
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            return f"Tool error: {e}"

    def _execute_coder_run_tests(
        self, test_path: str, command_type: str, workspace: Any, turn_index: int
    ) -> str:
        """Execute coder-facing test run (ADR 040 Point 1, 2, 4, 8, 10)."""
        project_root = getattr(workspace, "project_root", None)
        if not project_root:
            return "Test runner unavailable: workspace project root not found."

        try:
            from snodo.tools.shell import ShellMCP
            shell = ShellMCP(str(project_root))
            res = shell.run_tests_raw(test_path=test_path, command_type=command_type, timeout=120)
        except Exception as e:
            return f"Test execution failed to launch: {e}"

        self._emit_audit_event(
            "coder_test_run",
            {
                "turn_index": turn_index,
                "test_path": test_path,
                "command_type": command_type,
                "exit_code": res.get("exit_code", -1),
                "job_id": self._job_id,
                "task_id": self._task_id,
            },
        )

        exit_code = res.get("exit_code", -1)
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")

        return (
            f"Test Execution Result (exit code: {exit_code}):\n"
            f"--- STDOUT ---\n{stdout}\n"
            f"--- STDERR ---\n{stderr}"
        )

    def _emit_audit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit an audit event if audit_log is available."""
        try:
            from snodo.infrastructure.audit import get_audit_log
            audit_log = get_audit_log()
            if audit_log and hasattr(audit_log, "append_event"):
                audit_log.append_event(event_type, data)
        except Exception as e:
            _logger.warning("Audit event logging failed: %s", e)

    def _emit_turn_telemetry(
        self,
        turn_index: int,
        tool: str,
        target_path: str,
        read_hit: bool,
        tokens_in: int,
        tokens_out: int,
        elapsed_ms: float,
        submit_bytes: int = 0,
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
                "role": "coder",
                "validator_id": "",
                "turn_index": turn_index,
                "tool": tool,
                "target_path": canonical_target_path(target_path),
                "read_hit": bool(read_hit),
                "tokens_in": int(tokens_in or 0),
                "tokens_out": int(tokens_out or 0),
                "elapsed_ms": round(float(elapsed_ms or 0), 1),
                "submit_bytes": int(submit_bytes or 0),
            }
            persist_tool_telemetry(self._job_id or "unknown", record)
        except Exception as e:
            _logger.warning("Failed to persist tool telemetry: %s", e)

    def _parse_response(self, response: str) -> CodeArtifact:
        parsed = self._extract_json(response)

        if parsed is None or not isinstance(parsed, list):
            _logger.warning(
                "Coder parse failure — raw response (first 2KB): %s",
                _truncated_log(response),
            )
            raise ParseError(
                "Failed to parse response as JSON array of file operations"
            )

        files = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ParseError(f"Expected dict in file operations array, got {type(item).__name__}")
            action = item.get("action", "write")
            if "path" not in item or (action != "delete" and "content" not in item):
                raise ParseError(
                    f"Each file operation must have 'path' (and 'content' for write operations). Got keys: {list(item.keys())}"
                )
            rel_parts = Path(item["path"]).parts
            if rel_parts and rel_parts[0] == ".snodo":
                _logger.warning("Excluded protected path under .snodo/ from coder artifacts: %s", item["path"])
                continue
            files.append(FileArtifact(
                path=item["path"],
                content=item.get("content") or "",
                action=action,
            ))

        metadata = getattr(self, "_last_artifact_metadata", {}) or {}
        return CodeArtifact(files=files, metadata=metadata)

    @staticmethod
    def _extract_json(response: str):
        """Extract JSON array from raw response or code block.

        Uses a greedy fence extractor so that ``` inside file content does
        not break the match — only the outermost ``` fence pair is consumed.
        """
        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            pass

        # Greedy: match from the first ``` fence to the LAST ``` fence,
        # stripping only the outermost pair.  The non-greedy .*? would stop
        # at the first ``` inside file content.
        match = re.search(
            r'```(?:json)?\s*\n(.*)```\s*$', response, re.DOTALL
        )
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    def attach_mcp_tool(self, mcp_server: MCPServer) -> None:
        if mcp_server not in self.mcp_servers:
            self.mcp_servers.append(mcp_server)

    def list_available_tools(self) -> List[str]:
        return [f"mcp_server_{i}" for i in range(len(self.mcp_servers))]


def _truncated_log(raw: str, max_chars: int = 2048) -> str:
    """Truncate a raw response string for logging."""
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "...<truncated>"


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


def _search_term_directory_guidance(
    workspace: Any, tool_name: str, term_key: str, term: Any, directory: Any,
) -> Optional[str]:
    """Return a correction message when the search-term slot holds a directory path.

    ADR 040 tools shipped with a transcript where the coder called
    search_string(tests), search_string(src/scripts) — ninety turns of
    full-tree scans for directory NAMES (Fixes #184). The value in the term
    slot resolves to an existing directory and no explicit folder was given,
    so the model was almost certainly naming where to look, not what to find.
    Answering with guidance instead of a scan bounds the loop: one wrong call
    costs one turn, and the repeated call dedupes from there.
    """
    term_str = str(term or "").strip()
    if not term_str:
        return None
    dir_str = str(directory if directory is not None else ".").strip()
    if dir_str and dir_str not in (".", "./", term_str):
        # An explicit folder alongside the term: a legitimate scoped search
        # for text that happens to look like a path fragment.
        return None
    validator = getattr(workspace, "validate_path", None)
    if validator is None:
        return None
    try:
        resolved = validator(term_str)
    except Exception:
        return None
    if not isinstance(resolved, Path):
        return None
    try:
        is_dir = bool(resolved.is_dir())
    except Exception:
        is_dir = False
    if not is_dir:
        return None
    return (
        f"{tool_name} searches inside file contents: `{term_key}` is the text to find, not a path. "
        f"'{term_str}' is a directory in this project, so nothing was searched. "
        f"To search inside it, call {tool_name}({term_key}=<text to find>, directory=\"{term_str}\"). "
        f"To see what it contains, call list_files(directory=\"{term_str}\"). "
        f"To find where a symbol is defined, call search_symbol(name=<identifier>)."
    )


def _canonical_rel_path(value: Any, project_root: Optional[Path] = None) -> str:
    """Normalize a path argument the same way the workspace resolves it.

    Deduplication must not depend on spelling: './x', 'x/', 'a//b', 'a\\b' and
    an absolute path under the project root all address one file, and the
    resolver treats them as one, so the dedup key must too (Fixes #184).
    """
    p = str(value).replace("\\", "/").strip()
    if not p:
        return "."
    path = Path(p)
    if project_root is not None and path.is_absolute():
        try:
            path = path.resolve().relative_to(project_root)
        except (ValueError, OSError) as e:
            # Outside the root (or unresolvable): keep the absolute form so
            # the dedup key is at least stable across calls.
            _logger.debug("Dedup key kept absolute for path outside root %s: %s", project_root, e)
    return path.as_posix()


def _normalize_path_arg(args: Dict[str, Any], project_root: Optional[Path] = None) -> str:
    """Extract and normalize a path argument string."""
    for path_key in ("path", "directory", "file_path", "target_file", "file"):
        val = args.get(path_key)
        if val and isinstance(val, str):
            if val.strip():
                return _canonical_rel_path(val, project_root)
    return ""


def _extract_line_range(tool_name: str, args: Dict[str, Any]) -> Tuple[int, float]:
    """Extract (start_line, end_line) range from tool args."""
    if tool_name in ("read_file", "read_files"):
        return (1, float("inf"))

    if tool_name in ("read_file_lines", "read_lines"):
        start = args.get("start") or args.get("start_line") or 1
        end = args.get("end") or args.get("end_line") or float("inf")
        try:
            start_int = int(start)
        except (ValueError, TypeError):
            start_int = 1
        try:
            end_num = float(end)
        except (ValueError, TypeError):
            end_num = float("inf")
        return (start_int, end_num)

    return (1, float("inf"))


_READ_TOOLS = frozenset({
    "read_files",
    "read_file",
    "read_file_lines",
    "read_lines",
    "list_files",
    "list_directory",
    "ls",
    "git_show",
    "read_diff_between_refs",
    "git_log",
    "search_symbol",
    "search_string",
})


class ReadMemoryTracker:
    """Tracks read file contents, line ranges, directory listings, and tool arguments across turns."""

    def __init__(self, project_root: Optional[Any] = None) -> None:
        #: Same root the workspace resolves against; lets dedup keys treat
        #: every spelling of one path as one path (Fixes #184).
        self._project_root: Optional[Path] = None
        if project_root:
            try:
                self._project_root = Path(str(project_root)).resolve()
            except Exception:
                self._project_root = None
        # Exact canonical (tool_name, norm_args_json) -> turn_idx (1-based)
        self.exact_reads: Dict[Tuple[str, str], int] = {}
        # file_path -> [(start_line, end_line, turn_idx), ...]
        self.file_ranges: Dict[str, List[Tuple[int, float, int]]] = {}
        # canonical dir_path -> turn_idx
        self.dir_listings: Dict[str, int] = {}

    def check_read(self, tool_name: str, args: Dict[str, Any]) -> Optional[int]:
        """Check if a read tool call is already covered by memory.

        Returns the 1-based turn_idx if covered, else None.
        """
        if tool_name not in _READ_TOOLS:
            return None

        # 1. Exact argument match
        exact_key = _canonical_read_key(tool_name, args, self._project_root)
        if exact_key and exact_key in self.exact_reads:
            return self.exact_reads[exact_key]

        # 2. Batch read check
        if tool_name == "read_files":
            paths = args.get("paths", [])
            if isinstance(paths, list) and paths:
                all_read = True
                first_turn = None
                for p in paths:
                    norm_p = _canonical_rel_path(p, self._project_root) if isinstance(p, str) and p.strip() else ""
                    if not norm_p or norm_p not in self.file_ranges:
                        all_read = False
                        break
                    if first_turn is None:
                        first_turn = self.file_ranges[norm_p][0][2]
                if all_read:
                    return first_turn

        # 3. File line range / whole file coverage check
        if tool_name in ("read_file", "read_file_lines", "read_lines"):
            path = _normalize_path_arg(args, self._project_root)
            if path and path in self.file_ranges:
                req_start, req_end = _extract_line_range(tool_name, args)
                for cov_start, cov_end, turn_idx in self.file_ranges[path]:
                    if cov_start <= req_start and cov_end >= req_end:
                        return turn_idx

        # 4. Directory listing check
        if tool_name in ("list_files", "list_directory", "ls"):
            dir_path = _normalize_path_arg(args, self._project_root) or "."
            if dir_path in self.dir_listings:
                return self.dir_listings[dir_path]

        return None

    def record_read(self, tool_name: str, args: Dict[str, Any], turn_idx: int) -> None:
        """Record a successful read tool execution in memory."""
        exact_key = _canonical_read_key(tool_name, args, self._project_root)
        if exact_key:
            self.exact_reads[exact_key] = turn_idx

        if tool_name == "read_files":
            paths = args.get("paths", [])
            if isinstance(paths, list):
                for p in paths:
                    if isinstance(p, str) and p.strip():
                        norm_p = _canonical_rel_path(p, self._project_root)
                        if norm_p not in self.file_ranges:
                            self.file_ranges[norm_p] = []
                        self.file_ranges[norm_p].append((1, float("inf"), turn_idx))

        if tool_name in ("read_file", "read_file_lines", "read_lines"):
            path = _normalize_path_arg(args, self._project_root)
            if path:
                req_start, req_end = _extract_line_range(tool_name, args)
                if path not in self.file_ranges:
                    self.file_ranges[path] = []
                self.file_ranges[path].append((req_start, req_end, turn_idx))

        if tool_name in ("list_files", "list_directory", "ls"):
            dir_path = _normalize_path_arg(args, self._project_root) or "."
            if dir_path not in self.dir_listings:
                self.dir_listings[dir_path] = turn_idx

    def export_paths(self) -> Dict[str, List[str]]:
        """Return the paths this attempt inspected, WITHOUT their contents.

        ``files`` are paths opened via read_file/read_file_lines/read_lines;
        ``directories`` are paths listed via list_files/list_directory/ls.

        Contents are deliberately excluded (Fixes #157 follow-up on recovery
        cold-start): between attempts the tree changes, and handing a later
        attempt a cached version of a file it must now fix would make a stale
        view authoritative — worse than re-reading. Only the map of *where the
        previous attempt looked* is safe to carry forward.
        """
        return {
            "files": sorted(self.file_ranges.keys()),
            "directories": sorted(self.dir_listings.keys()),
        }


def _canonical_read_key(
    name: str, args: Dict[str, Any], project_root: Optional[Path] = None,
) -> Optional[Tuple[str, str]]:
    """Return (tool_name, canonical_args_str) for read tools, or None if not a read tool.

    The key must describe the CALL, not the spelling: an optional parameter
    left absent is the same call as one sent at its default, a null parameter
    is the same as an absent one, and every path alias the resolver unifies
    must unify here too — otherwise the dedup that is supposed to bound the
    search/read loop lets one call become ninety (Fixes #184).
    """
    if name not in _READ_TOOLS:
        return None

    norm_args: Dict[str, Any] = {}
    for key, val in args.items():
        if val is None:
            continue  # a null optional parameter is an omitted one
        if key == "paths" and isinstance(val, list):
            norm_args[key] = [
                _canonical_rel_path(p, project_root)
                for p in val
                if isinstance(p, str) and p.strip()
            ]
        elif key in ("path", "directory", "file_path", "target_file", "file") and isinstance(val, str):
            norm_args[key] = _canonical_rel_path(val, project_root)
        else:
            norm_args[key] = val

    # 'directory' defaults to "." — explicit and implicit are one call.
    if norm_args.get("directory") == ".":
        norm_args.pop("directory")

    try:
        args_str = json.dumps(norm_args, sort_keys=True)
    except Exception:
        args_str = str(norm_args)

    return (name, args_str)


def format_repeat_read_response(tool_name: str, args: Dict[str, Any], prev_turn: int) -> str:
    """Format a concise tool response pointing to the previous turn containing the result."""
    if tool_name in ("search_string", "search_symbol"):
        term_key = "query" if tool_name == "search_string" else "name"
        term = str((args or {}).get(term_key) or "").strip()
        if term:
            return (
                f"{tool_name} for '{term}' was already run in Turn {prev_turn}. "
                f"Refer to the tool response from Turn {prev_turn} for its results. "
                "Repeating this call returns the same output and wastes a turn — "
                "search for a different term, read a specific file, or proceed to submit_files."
            )
    target = _normalize_path_arg(args)
    if tool_name in ("read_file", "read_files", "read_file_lines", "read_lines"):
        req_start, req_end = _extract_line_range(tool_name, args)
        range_str = f"lines {req_start}-{int(req_end)}" if req_end != float("inf") else "full file"
        if target:
            return (
                f"File '{target}' ({range_str}) was already fetched using {tool_name} in Turn {prev_turn}. "
                f"Refer to the tool response from Turn {prev_turn} for its content."
            )
    if tool_name in ("list_files", "list_directory", "ls"):
        dir_name = target or "."
        return (
            f"Directory '{dir_name}' was already listed using {tool_name} in Turn {prev_turn}. "
            f"Refer to the tool response from Turn {prev_turn} for its contents."
        )
    if target:
        return (
            f"'{target}' was already fetched using {tool_name} in Turn {prev_turn}. "
            f"Refer to the tool response from Turn {prev_turn} for its content."
        )
    return (
        f"Tool '{tool_name}' with identical arguments was already executed in Turn {prev_turn}. "
        f"Refer to the tool response from Turn {prev_turn} for its content."
    )
