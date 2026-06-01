"""LiteLLM coder adapter.

FILE: snodo/coders/litellm.py

Implements CoderAdapter using LangChain + liteLLM for model abstraction.
"""

import json
import re
from typing import List, Optional

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact, MCPServer
from snodo.coders.base import CoderAdapter, LLMCallError, ParseError


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
        max_tokens: int = 4000
    ):
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.temperature = temperature
        self.max_tokens = max_tokens

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

        prompt_parts.append("""
## Output Format
Your response MUST be a JSON array of file operations. Each element has:
- "path": file path relative to the project root
- "content": the full file content
- "action": "write" (default) or "delete"

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

        try:
            response = self._completion_fn(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            raise LLMCallError(f"LLM call failed: {e}")

    def _parse_response(self, response: str) -> CodeArtifact:
        parsed = self._extract_json(response)

        if parsed is None or not isinstance(parsed, list):
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
        """Extract JSON array from raw response or code block."""
        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            pass

        match = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
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
