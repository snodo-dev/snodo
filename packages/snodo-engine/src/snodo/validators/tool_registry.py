"""Registry and plugin contract for LLM validator tools.

Plugins expose an object through the ``snodo.validator_tools`` entry-point
group. The object may be a :class:`ValidatorTool` instance or a zero-argument
factory returning one. A tool's ``definition`` is the JSON-schema function
definition shown to the model, and its ``handler`` receives parsed arguments
and a :class:`ValidatorToolContext`, returning text for the model.

The context contains only run inputs and a workspace root for read-only use;
handlers must not modify the workspace or ``.snodo/``. Protocol configuration
supplies per-validator tool options. Built-in validator tool names are reserved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

_logger = logging.getLogger(__name__)

# Kept here until the validator loop imports this registry; this list mirrors
# the built-in function names in llm_validator.py.
BUILTIN_VALIDATOR_TOOL_NAMES = frozenset(
    {
        "read_diff_between_refs",
        "git_show",
        "git_log",
        "read_file",
        "read_file_lines",
        "list_files",
        "summarize_directory",
        "submit_verdict",
    }
)


@dataclass(frozen=True)
class ValidatorToolContext:
    """Read-only run inputs available to a validator tool handler.

    ``workspace_root`` is supplied for reading only; tool handlers have no
    workspace-writing API and must not write to it or its ``.snodo/`` data.
    ``diff_refs`` records the refs being compared, while ``diff_accessor`` may
    provide the validator's existing diff-reading capability.
    """

    workspace_root: Path
    spec_text: str
    validator_id: str
    tool_options: Mapping[str, Any] = field(default_factory=dict)
    diff_refs: tuple[str, str] | None = None
    diff_accessor: Callable[..., str] | None = None


@dataclass(frozen=True)
class ValidatorTool:
    """A model-visible function definition and its text-returning handler."""

    definition: Mapping[str, Any]
    handler: Callable[[Mapping[str, Any], ValidatorToolContext], str]

    @property
    def name(self) -> str:
        """Function name declared by the model-facing definition."""
        function = self.definition.get("function", self.definition)
        name = function.get("name") if isinstance(function, Mapping) else None
        if not isinstance(name, str) or not name:
            raise ValueError("validator tool definition must declare a function name")
        return name


class ValidatorToolRegistry:
    """Discover and cache validator tool plugins by their function name."""

    def __init__(self) -> None:
        self._tools: dict[str, ValidatorTool] = {}
        self._plugin_load_failures: dict[str, str] = {}
        self._loaded_plugins: set[str] = set()

    def register(self, tool: ValidatorTool) -> None:
        """Register a tool, refusing names owned by built-in validator tools."""
        if not isinstance(tool, ValidatorTool):
            raise TypeError("validator tool must be a ValidatorTool instance")
        name = tool.name
        if name in BUILTIN_VALIDATOR_TOOL_NAMES:
            raise ValueError(f"validator tool name '{name}' is reserved by a built-in validator tool")
        self._tools[name] = tool

    def lookup(self, name: str) -> ValidatorTool | None:
        """Return a registered tool by model-visible function name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ValidatorTool]:
        """Return registered tools in stable name order."""
        return [self._tools[name] for name in sorted(self._tools)]

    def discover_plugins(self) -> None:
        """Load installed ``snodo.validator_tools`` entry points.

        Entry points may load a tool instance or a zero-argument factory.
        Import, factory, and registration failures are reported and skipped.
        """
        self._plugin_load_failures.clear()
        self._loaded_plugins.clear()
        try:
            from importlib.metadata import entry_points

            plugins = entry_points(group="snodo.validator_tools")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._plugin_load_failures["*"] = message
            _logger.warning("Could not discover snodo.validator_tools entry points: %s", message)
            return

        for plugin in plugins:
            try:
                tool = plugin.load()
                if callable(tool) and not isinstance(tool, ValidatorTool):
                    tool = tool()
                if not isinstance(tool, ValidatorTool):
                    raise TypeError("entry point must load a ValidatorTool or zero-argument factory")
                self.register(tool)
                self._loaded_plugins.add(plugin.name)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                self._plugin_load_failures[plugin.name] = message
                _logger.warning("Could not load validator tool plugin '%s': %s", plugin.name, message)

    def plugin_load_failures(self) -> dict[str, str]:
        """Return a copy of entry-point discovery and load failures."""
        return self._plugin_load_failures.copy()

    def plugin_status(self) -> dict[str, dict[str, str]]:
        """Return installed and failed validator tool entry points."""
        result = {name: {"status": "installed", "error": ""} for name in self._loaded_plugins}
        result.update(
            {name: {"status": "failed", "error": error}
             for name, error in self._plugin_load_failures.items()}
        )
        return result


_default_registry = ValidatorToolRegistry()
_default_registry.discover_plugins()
