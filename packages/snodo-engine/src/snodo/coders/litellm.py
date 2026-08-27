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
from snodo.coders.base import CoderAdapter, LLMCallError, ParseError
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
_logging.getLogger("LiteLLM").setLevel(_logging.WARNING)

# CF models absent from models.dev catalog — price via register_model.
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

_logger = logging.getLogger(__name__)


# Maximum tool-use turns before forcing a CodeArtifact parse.
_DEFAULT_MAX_TOOL_TURNS = 20


def _is_gemini3_plus(model: str) -> bool:
    m = re.search(r'gemini-(\d+)', model)
    return bool(m and int(m.group(1)) >= 3)


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
    ):
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tool_turns = max_tool_turns if max_tool_turns is not None else _DEFAULT_MAX_TOOL_TURNS
        self.workspace_mcp = workspace_mcp
        self.progress_callback = progress_callback

        self._job_id: str = ""
        self._task_id: str = ""

        try:
            from litellm import completion
            self._completion_fn = completion
        except ImportError:
            self._completion_fn = None

    def _resolve_api_base(self) -> Optional[str]:
        """Return api_base for the current model, if provider has base_url set."""
        from snodo.config import ConfigManager
        return ConfigManager.resolve_api_base(self.model)

    def _resolve_cf_headers(self) -> Optional[dict]:
        """Return extra_headers for Cloudflare Workers AI session affinity."""
        from snodo.config import ConfigManager
        provider = ConfigManager._provider_for_model(self.model)
        if provider == "cloudflare":
            return {"x-session-affinity": self._task_id or "unknown"}
        return None

    def implement(self, spec: TaskSpec) -> CodeArtifact:
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
                "You may call read-only tools to inspect the current state of files "
                "before generating your changes. Use read_file(path) to see existing "
                "content, read_file_lines(path, start, end) for line ranges, and "
                "list_files(directory) to explore the project.\n"
                "Read existing files you need to modify so you can make faithful edits.\n"
                "\n"
                "When you are ready to deliver your changes, call the\n"
                "`submit_files(files)` tool — this is the ONLY way to deliver file\n"
                "operations.  Do NOT emit file content as prose or as a JSON text blob.\n"
                "\n"
            )

        prompt_parts.append("""
## Output Format
Your response MUST be a JSON array of file operations. Each element has:
- "path": file path relative to the project root
- "content": the full file content
- "action": "write" (default) or "delete"

Return ONLY the JSON array, no other text.

```json
[
  {"path": "src/module.py", "content": "def my_function():\\n    pass\\n", "action": "write"},
  {"path": "tests/test_module.py", "content": "def test_my_function():\\n    assert my_function() is not None\\n", "action": "write"}
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
            kwargs = {
                "model": self.model,
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
            cf_headers = self._resolve_cf_headers()
            if cf_headers:
                kwargs["extra_headers"] = cf_headers
            if not _is_gemini3_plus(self.model):
                kwargs["temperature"] = self.temperature
            response = self._completion_fn(**kwargs)
            self._check_truncation(response)
            return response.choices[0].message.content
        except (LLMCallError, ParseError):
            raise
        except Exception as e:
            raise LLMCallError(f"LLM call failed: {e}")

    def _call_llm_with_tools(self, prompt: str) -> str:
        """Bounded tool-use loop with submit_files terminal tool."""
        workspace = self.workspace_mcp
        tools = self._build_tool_definitions()
        tools.append(self._SUBMIT_FILES_DEF)

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": prompt},
        ]

        retried_free_text = False
        finish_reason = None
        start_time = time.monotonic()
        read_tracker = ReadMemoryTracker()

        for turn in range(self.max_tool_turns):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
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
                if not _is_gemini3_plus(self.model):
                    kwargs["temperature"] = self.temperature
                response = self._completion_fn(**kwargs)
            except Exception as e:
                raise LLMCallError(f"LLM tool-loop error on turn {turn + 1}: {e}")

            self._check_truncation(response)

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", [])
            finish_reason = getattr(response.choices[0], "finish_reason", None)

            if self.progress_callback:
                elapsed_str = format_elapsed(time.monotonic() - start_time)
                tools_str = format_tool_call_summary(tool_calls)
                self.progress_callback(f"    [{elapsed_str}] Turn {turn + 1}: {tools_str}")

            # Check for submit_files before anything else
            files_list = self._extract_submit_files(tool_calls)
            if files_list is not None and files_list:
                return json.dumps(files_list)

            # Execute read tools
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
                        # submit_files present but not a valid completion
                        # (zero files or unparseable args) — feed back a tool
                        # response so every tool_call_id is answered before
                        # the next request.
                        result = self._submit_files_feedback(tc)
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
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result),
                    })
                continue

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

        # Hit turn cap — try last assistant content for legacy parse
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

    _SUBMIT_FILES_DEF = {
        "type": "function",
        "function": {
            "name": "submit_files",
            "description": (
                "Submit file operations. Call this exactly once when you are "
                "ready to deliver ALL your changes. Each file has path, content, "
                "and an optional action (\"write\" or \"delete\")."
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
                                    "description": "Full file content",
                                },
                                "action": {
                                    "type": "string",
                                    "enum": ["write", "delete"],
                                    "description": "write or delete",
                                },
                            },
                            "required": ["path", "content"],
                        },
                        "description": "Array of file operations",
                    },
                },
                "required": ["files"],
            },
        },
    }

    @staticmethod
    def _extract_submit_files(tool_calls: list) -> Optional[List[Dict]]:
        """Scan tool_calls for submit_files and return the files array if found.

        Returns None if submit_files is not present or has invalid arguments.
        """
        for tc in (tool_calls or []):
            if tc.function.name != "submit_files":
                continue
            try:
                args = json.loads(tc.function.arguments)
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
    ) -> str:
        """Execute a read-only tool call and return the result as a string."""
        try:
            if name == "read_file":
                return workspace.read_file(args["path"])
            elif name == "read_file_lines":
                return workspace.read_file_lines(args["path"], args["start"], args["end"])
            elif name == "list_files":
                return "\n".join(workspace.list_files(args.get("directory", ".")))
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            return f"Tool error: {e}"

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
            if "path" not in item or "content" not in item:
                raise ParseError(
                    f"Each file operation must have 'path' and 'content'. Got keys: {list(item.keys())}"
                )
            rel_parts = Path(item["path"]).parts
            if rel_parts and rel_parts[0] == ".snodo":
                _logger.warning("Excluded protected path under .snodo/ from coder artifacts: %s", item["path"])
                continue
            files.append(FileArtifact(
                path=item["path"],
                content=item["content"],
                action=item.get("action", "write"),
            ))

        return CodeArtifact(files=files)

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


def _normalize_path_arg(args: Dict[str, Any]) -> str:
    """Extract and normalize a path argument string."""
    for path_key in ("path", "directory", "file_path", "target_file", "file"):
        val = args.get(path_key)
        if val and isinstance(val, str):
            p = val.strip()
            if p:
                try:
                    return Path(p).as_posix()
                except Exception:
                    return p
    return ""


def _extract_line_range(tool_name: str, args: Dict[str, Any]) -> Tuple[int, float]:
    """Extract (start_line, end_line) range from tool args."""
    if tool_name == "read_file":
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


class ReadMemoryTracker:
    """Tracks read file contents, line ranges, directory listings, and tool arguments across turns."""

    def __init__(self) -> None:
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
        read_tools = {
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
        }
        if tool_name not in read_tools:
            return None

        # 1. Exact argument match
        exact_key = _canonical_read_key(tool_name, args)
        if exact_key and exact_key in self.exact_reads:
            return self.exact_reads[exact_key]

        # 2. File line range / whole file coverage check
        if tool_name in ("read_file", "read_file_lines", "read_lines"):
            path = _normalize_path_arg(args)
            if path and path in self.file_ranges:
                req_start, req_end = _extract_line_range(tool_name, args)
                for cov_start, cov_end, turn_idx in self.file_ranges[path]:
                    if cov_start <= req_start and cov_end >= req_end:
                        return turn_idx

        # 3. Directory listing check
        if tool_name in ("list_files", "list_directory", "ls"):
            dir_path = _normalize_path_arg(args) or "."
            if dir_path in self.dir_listings:
                return self.dir_listings[dir_path]

        return None

    def record_read(self, tool_name: str, args: Dict[str, Any], turn_idx: int) -> None:
        """Record a successful read tool execution in memory."""
        exact_key = _canonical_read_key(tool_name, args)
        if exact_key:
            self.exact_reads[exact_key] = turn_idx

        if tool_name in ("read_file", "read_file_lines", "read_lines"):
            path = _normalize_path_arg(args)
            if path:
                req_start, req_end = _extract_line_range(tool_name, args)
                if path not in self.file_ranges:
                    self.file_ranges[path] = []
                self.file_ranges[path].append((req_start, req_end, turn_idx))

        if tool_name in ("list_files", "list_directory", "ls"):
            dir_path = _normalize_path_arg(args) or "."
            if dir_path not in self.dir_listings:
                self.dir_listings[dir_path] = turn_idx


def _canonical_read_key(name: str, args: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Return (tool_name, canonical_args_str) for read tools, or None if not a read tool."""
    read_tools = {
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
    }
    if name not in read_tools:
        return None

    norm_args = dict(args)
    for path_key in ("path", "directory", "file_path", "target_file"):
        if path_key in norm_args and isinstance(norm_args[path_key], str):
            p = norm_args[path_key].strip()
            norm_args[path_key] = str(Path(p).as_posix())

    try:
        args_str = json.dumps(norm_args, sort_keys=True)
    except Exception:
        args_str = str(norm_args)

    return (name, args_str)


def format_repeat_read_response(tool_name: str, args: Dict[str, Any], prev_turn: int) -> str:
    """Format a concise tool response pointing to the previous turn containing the result."""
    target = _normalize_path_arg(args)
    if tool_name in ("read_file", "read_file_lines", "read_lines"):
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
