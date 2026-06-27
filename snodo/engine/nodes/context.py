"""Context node mixin.

FILE: snodo/engine/nodes/context.py
"""

from typing import Dict, Any, List, Optional
from snodo.engine.state import LoopState
from snodo.tools.workspace import WorkspaceMCP


class ContextMixin:
    """Mixin providing workspace project structure and summarization capabilities."""

    @staticmethod
    def _init_summary_model():
        """Try to create a cheap summary model. Returns None if unavailable."""
        try:
            from snodo.infrastructure.memory import create_summary_model
            return create_summary_model()
        except Exception:
            return None

    def _maybe_summarize(self, loop_state: LoopState) -> LoopState:
        """Summarize messages if they exceed token threshold."""
        total_chars = sum(len(m.get("content", "")) for m in loop_state.messages)
        token_estimate = total_chars // 4

        if token_estimate < 8000:
            return loop_state

        if self._summary_model is not None:
            try:
                summary_prompt = (
                    "Summarize the following conversation history concisely "
                    "(max 512 tokens). Focus on key decisions, artifacts "
                    "produced, and important context:\n\n"
                )
                for msg in loop_state.messages:
                    summary_prompt += f"{msg['role']}: {msg['content']}\n"

                response = self._summary_model.invoke(summary_prompt)
                loop_state.summary = response.content
                loop_state.messages = loop_state.messages[-3:]
                return loop_state
            except Exception:
                pass  # Fall through to truncation

        # Fallback: truncate messages, keep most recent 3
        discarded = loop_state.messages[:-3]
        if discarded:
            snippets = [m.get("content", "")[:100] for m in discarded]
            loop_state.summary = "Previous: " + "; ".join(snippets)
        loop_state.messages = loop_state.messages[-3:]
        return loop_state

    def _collect_project_context(
        self, workspace_mcp: Optional[WorkspaceMCP]
    ) -> Dict[str, Any]:
        """Collect project context: language, structure, key configs."""
        context: Dict[str, Any] = {
            "language": "unknown",
            "structure": "",
            "config_files": {},
        }
        if not workspace_mcp:
            return context

        # Language detection from marker files
        lang_markers = [
            ("package.json", "javascript"),
            ("tsconfig.json", "typescript"),
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
            ("setup.cfg", "python"),
            ("Cargo.toml", "rust"),
            ("go.mod", "go"),
            ("pom.xml", "java"),
            ("build.gradle", "java"),
        ]
        for marker, lang in lang_markers:
            if workspace_mcp.file_exists(marker):
                context["language"] = lang
                break

        # Directory tree via BFS (depth 3)
        context["structure"] = self._build_dir_tree(workspace_mcp, max_depth=3)

        # Key config files
        config_candidates = [
            "package.json", "tsconfig.json", "pyproject.toml",
            "setup.py", "setup.cfg", "Cargo.toml", "go.mod",
        ]
        for cfg in config_candidates:
            try:
                content = workspace_mcp.read_file(cfg)
                # Truncate large configs to first 2000 chars
                context["config_files"][cfg] = content[:2000]
            except FileNotFoundError:
                pass

        return context

    @staticmethod
    def _build_dir_tree(
        workspace_mcp: WorkspaceMCP, max_depth: int = 3
    ) -> str:
        """Build directory tree via iterative BFS."""
        lines: List[str] = []
        # Queue entries: (relative_path, depth)
        queue: List[tuple] = [(".", 0)]

        while queue:
            current_path, depth = queue.pop(0)
            try:
                entries = sorted(workspace_mcp.list_files(current_path))
            except (FileNotFoundError, ValueError):
                continue

            for entry in entries:
                # Skip hidden directories and common noise
                if entry.startswith(".") or entry in ("node_modules", "__pycache__", ".git"):
                    continue
                indent = "  " * depth
                child_path = entry if current_path == "." else f"{current_path}/{entry}"
                # Check if it's a directory by trying to list it
                try:
                    workspace_mcp.list_files(child_path)
                    lines.append(f"{indent}{entry}/")
                    if depth < max_depth - 1:
                        queue.append((child_path, depth + 1))
                except (FileNotFoundError, ValueError):
                    lines.append(f"{indent}{entry}")

        return "\n".join(lines[:200])  # Cap at 200 lines
