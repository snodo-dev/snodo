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
from typing import Any, Dict, List, Optional

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact, MCPServer
from snodo.coders.base import CoderAdapter, LLMCallError, ParseError

_logger = logging.getLogger(__name__)


# Maximum tool-use turns before forcing a CodeArtifact parse.
_DEFAULT_MAX_TOOL_TURNS = 6


class LiteLLMAdapter(CoderAdapter):
    """Implements CoderAdapter using LangChain + liteLLM.

    This adapter bridges V1 patterns (LangChain ecosystem) to V2 protocol.
    It handles:
    - Model abstraction via liteLLM
    - Tool orchestration via LangChain
    - MCP server integration
    - Output parsing into CodeArtifact
    """

    def __init__(
        self,
        model: str = "gpt-4",
        mcp_servers: Optional[List[MCPServer]] = None,
        temperature: float = 0.7,
        max_tokens: int = 16000,
        max_tool_turns: Optional[int] = None,
        workspace_mcp: Optional[Any] = None,
    ):
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tool_turns = max_tool_turns if max_tool_turns is not None else _DEFAULT_MAX_TOOL_TURNS
        self.workspace_mcp = workspace_mcp

        try:
            from litellm import completion
            self._completion_fn = completion
        except ImportError:
            self._completion_fn = None

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

        # Memory summary section
        if spec.memory_summary:
            prompt_parts.append(f"\n## Session History\n{spec.memory_summary}\n")

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

Now generate the implementation:
""")

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
            response = self._completion_fn(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise LLMCallError(f"LLM call failed: {e}")

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
            response = self._completion_fn(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
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

        for turn in range(self.max_tool_turns):
            try:
                response = self._completion_fn(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception as e:
                raise LLMCallError(f"LLM tool-loop error on turn {turn + 1}: {e}")

            self._check_truncation(response)

            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", [])

            # Check for submit_files before anything else
            files_list = self._extract_submit_files(tool_calls)
            if files_list is not None:
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
                        continue  # already handled above
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    result = self._execute_tool(tool_name, args, workspace)
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

            # Fallback: return whatever free-text we have for legacy parse
            if msg.content is not None:
                return msg.content
            break

        # Hit turn cap — return last assistant content for legacy parse
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return ""

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

    def _check_truncation(self, response: Any) -> None:
        """Raise ParseError if the completion was truncated at max_tokens."""
        try:
            choice = response.choices[0]
            finish = getattr(choice, "finish_reason", None)
            if finish == "length":
                raw = str(getattr(choice.message, "content", ""))
                _logger.warning(
                    "Coder output truncated at max_tokens=%s — "
                    "raw response (first 2KB): %s",
                    self.max_tokens,
                    _truncated_log(raw),
                )
                raise ParseError(
                    f"Coder output truncated at max_tokens={self.max_tokens}. "
                    "Raise the limit or split the task into smaller subtasks."
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
