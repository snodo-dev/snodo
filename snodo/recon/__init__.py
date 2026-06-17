"""Recon manager for read-only multi-agent codebase exploration.

FILE: snodo/recon/__init__.py

Mirrors snodo/jobs/__init__.py (JobManager pattern).  Each recon gets
a directory: .snodo/recons/<recon_id>/ containing state.json and
results.json.  The background process fans out N agents in parallel
using litellm.completion with a read-only tool surface.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Thread
from typing import Optional

from pydantic import BaseModel

_logger = logging.getLogger(__name__)


class ReconState(BaseModel):
    recon_id: str
    query: str
    paths: list[str]
    agents: list[str]
    status: str  # "running" | "complete" | "failed"
    created_at: float
    completed_at: Optional[float] = None


class ReconResult(BaseModel):
    agent: str
    model: str
    result: str
    error: Optional[str] = None


class ReconError(Exception):
    """Recon system error."""


# Module-level thread registry so shutdown() can join threads from any
# ReconManager instance (needed for test teardown where handler creates
# its own manager internally).
_threads: list[Thread] = []


# Read-only tool definitions for the recon agent surface.
_READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read file content within the project",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
            },
            "required": ["path"],
        },
    },
}

_LIST_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List files in a directory",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path (default: .)",
                },
            },
            "required": ["directory"],
        },
    },
}

_READ_ONLY_TOOLS = [_READ_FILE_TOOL, _LIST_FILES_TOOL]


def _resolve_agent_model(agent: str) -> str:
    """Resolve 'default' to the configured model; pass-through otherwise."""
    if agent == "default":
        from snodo.cli.config import ConfigManager
        return ConfigManager().get_model()
    return agent


def resolve_recon_agents(
    requested_n: int | None = None,
    recon_models: list[str] | None = None,
    recon_default_n: int = 1,
    explicit_agents: list[str] | None = None,
) -> list[str]:
    """Resolve recon agents from config + CLI/MCP request.

    Precedence (most specific wins):
      1. explicit_agents non-empty → return as-is
      2. n = requested_n or recon_default_n or 1
      3. resolve n against recon_models:
         - models empty: n≤1 → ["default"]; n>1 → ["default"] + warn
         - models present: first n; slots beyond len → warn per slot, skip
    """
    if explicit_agents:
        return explicit_agents

    n = requested_n or recon_default_n or 1
    models = recon_models or []

    if not models:
        if n <= 1:
            return ["default"]
        import sys
        print(
            f"Warning: num_agents={n} but no recon models configured. "
            "Using 'default' once (duplicates add no value).",
            file=sys.stderr,
        )
        return ["default"]

    results = []
    for i in range(n):
        if i < len(models):
            results.append(models[i])
        else:
            print(
                f"Warning: slot {i + 1}/{n} beyond configured models "
                f"({len(models)}). Skipped.",
                file=sys.stderr,
            )
    return results if results else ["default"]


def _read_file(project_root: str, path: str) -> str:
    """Read a file within *project_root*, rejecting path traversal."""
    resolved = (Path(project_root) / path).resolve()
    if not str(resolved).startswith(str(Path(project_root).resolve())):
        return "Error: path traversal rejected"
    try:
        return resolved.read_text()
    except Exception as e:
        return f"Error reading file: {e}"


def _list_files(project_root: str, directory: str) -> str:
    """List files in *directory*, rejecting path traversal."""
    resolved = (Path(project_root) / directory).resolve()
    if not str(resolved).startswith(str(Path(project_root).resolve())):
        return "Error: path traversal rejected"
    try:
        if not resolved.is_dir():
            return f"Error: not a directory: {directory}"
        entries = sorted(resolved.iterdir())
        lines = []
        for e in entries:
            marker = "/" if e.is_dir() else ""
            lines.append(f"{e.name}{marker}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing files: {e}"


def _call_agent(
    project_root: str,
    model: str,
    query: str,
    paths: list[str],
    agent_label: str,
    max_turns: int = 10,
) -> ReconResult:
    """Run a single agent: LLM with read-only tools, returning raw text."""
    import litellm
    litellm.suppress_debug_info = True

    path_context = ", ".join(paths) if paths else "./"
    system_msg = (
        f"You are a codebase exploration agent. Your task is to understand "
        f"the codebase and answer a query. You may only READ files — you "
        f"cannot write, edit, delete, or run commands.\n\n"
        f"Project root: {project_root}\n"
        f"Search paths: {path_context}\n"
    )
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": f"Query: {query}"},
    ]

    final_answer = ""

    from snodo.cli.config import provider_env
    _logger.debug("recon: injecting API key for model=%s", model)
    with provider_env(model) as mgr:
        for _turn in range(max_turns):
            try:
                response = litellm.completion(
                    model=model,
                    messages=messages,
                    tools=_READ_ONLY_TOOLS,
                )
            except Exception as e:
                return ReconResult(
                    agent=agent_label,
                    model=model,
                    result="",
                    error=str(e),
                )

            choice = response.choices[0]
            msg = choice.message
            text = msg.content or ""

            if text:
                final_answer += text

            if not hasattr(msg, "tool_calls") or not msg.tool_calls:
                if _turn == 0 and not text:
                    _logger.warning(
                        "Recon agent disengaged on turn 0 — model=%s, "
                        "content=%r",
                        model, msg.content,
                    )
                break

            # Execute read-only tool calls
            messages.append({"role": "assistant", "content": text, "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]})

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                if name == "read_file":
                    result = _read_file(project_root, args.get("path", ""))
                elif name == "list_files":
                    result = _list_files(project_root, args.get("directory", "."))
                else:
                    result = f"Error: unknown tool: {name}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        if not final_answer.strip():
            _logger.warning(
                "Recon agent returned empty result on model=%s — "
                "possible model disengagement or auth issue",
                model,
            )
            return ReconResult(
                agent=agent_label, model=model,
                result="", error="Agent returned empty result",
            )

        return ReconResult(
            agent=agent_label,
            model=model,
            result=final_answer.strip(),
        )


class ReconManager:
    """Manages recon operations in .snodo/recons/ directories.

    Mirrors JobManager pattern — each recon is a subdirectory with
    state.json and results.json.
    """

    def __init__(self, project_root: str):
        snodo_dir = Path(project_root) / ".snodo"
        if not snodo_dir.is_dir():
            raise ValueError(f"Not a snodo project: {project_root} (no .snodo/ directory)")
        self.recons_dir = snodo_dir / "recons"
        self.recons_dir.mkdir(exist_ok=True)
        self.project_root = project_root

    def _generate_id(self) -> str:
        """Generate a unique recon ID: rec_<6-hex> from time.time_ns()."""
        for _ in range(10):
            raw = time.time_ns()
            recon_id = f"rec_{raw & 0xffffff:06x}"
            if not (self.recons_dir / recon_id).exists():
                return recon_id
            time.sleep(0.001)
        raise ReconError("Failed to generate unique recon ID after 10 attempts")

    def _recon_dir(self, recon_id: str) -> Path:
        recon_path = self.recons_dir / recon_id
        if not recon_path.is_dir():
            raise ReconError(f"Recon not found: {recon_id}")
        return recon_path

    def _save_state(self, recon_dir: Path, state: dict) -> None:
        state_path = recon_dir / "state.json"
        tmp_path = recon_dir / "state.json.tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        os.rename(str(tmp_path), str(state_path))

    def _load_state(self, recon_dir: Path) -> dict:
        state_path = recon_dir / "state.json"
        if not state_path.exists():
            raise ReconError(f"No state.json in {recon_dir.name}")
        with open(state_path) as f:
            return json.load(f)

    def _save_results(self, recon_dir: Path, results: list) -> None:
        results_path = recon_dir / "results.json"
        tmp_path = recon_dir / "results.json.tmp"
        payload = []
        for r in results:
            if isinstance(r, ReconResult):
                payload.append(r.model_dump())
            else:
                payload.append(r)
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2)
        os.rename(str(tmp_path), str(results_path))

    def _load_results(self, recon_dir: Path) -> list:
        results_path = recon_dir / "results.json"
        if not results_path.exists():
            return []
        with open(results_path) as f:
            return json.load(f)

    def _run_recon(self, recon_id: str, query: str, paths: list[str],
                   agents: list[str]) -> None:
        """Background entry point — fans out agents, writes results, updates state."""
        try:
            self._run_recon_impl(recon_id, query, paths, agents)
        except Exception:
            pass  # Silently discard (test teardown may have removed the dir)

    def _run_recon_impl(self, recon_id: str, query: str, paths: list[str],
                        agents: list[str]) -> None:
        recon_dir = self.recons_dir / recon_id

        resolved_agents = []
        for agent_label in agents:
            model = _resolve_agent_model(agent_label)
            resolved_agents.append((agent_label, model))

        results = []
        with ThreadPoolExecutor(max_workers=min(len(resolved_agents), 4)) as executor:
            futures = {}
            for agent_label, model in resolved_agents:
                future = executor.submit(
                    _call_agent,
                    self.project_root, model, query, paths, agent_label,
                )
                futures[future] = agent_label

            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    agent_label = futures[future]
                    result = ReconResult(
                        agent=agent_label,
                        model="",
                        result="",
                        error=str(e),
                    )
                results.append(result)

        self._save_results(recon_dir, results)

        state = self._load_state(recon_dir)
        succeeded = sum(1 for r in results if isinstance(r, ReconResult) and not r.error)
        state["status"] = "complete" if succeeded > 0 else "failed"
        state["completed_at"] = time.time()
        self._save_state(recon_dir, state)

    def submit(self, query: str, paths: list[str],
               agents: Optional[list[str]] = None) -> str:
        """Submit a recon query — returns immediately with a recon_id.

        Args:
            query: The exploration question
            paths: List of paths to search within
            agents: List of model strings; ``["default"]`` resolves to
                    the configured model.  Named agents pass through directly.

        Returns:
            Recon ID string (rec_...)
        """
        if agents is None:
            agents = ["default"]

        recon_id = self._generate_id()
        recon_dir = self.recons_dir / recon_id
        recon_dir.mkdir()

        state = {
            "recon_id": recon_id,
            "query": query,
            "paths": paths,
            "agents": agents,
            "status": "running",
            "created_at": time.time(),
            "completed_at": None,
        }
        self._save_state(recon_dir, state)

        thread = Thread(
            target=self._run_recon,
            args=(recon_id, query, paths, agents),
        )
        thread.start()
        _threads.append(thread)

        return recon_id

    def shutdown(self, timeout: float = 5.0) -> None:
        """Wait for all background recon threads to complete (test helper)."""
        for thread in _threads:
            thread.join(timeout=timeout)
        _threads.clear()

    def get_status(self, recon_id: str) -> dict:
        """Get the current status of a recon."""
        recon_dir = self._recon_dir(recon_id)
        state = self._load_state(recon_dir)
        results = self._load_results(recon_dir) if state.get("status") == "complete" else []
        return {**state, "results": results}

    def get_results(self, recon_id: str) -> dict:
        """Get the raw results of a completed recon."""
        recon_dir = self._recon_dir(recon_id)
        state = self._load_state(recon_dir)
        if state.get("status") != "complete":
            raise ReconError(
                f"Recon {recon_id} is not complete (status: {state.get('status')})"
            )
        results = self._load_results(recon_dir)
        return {
            "recon_id": recon_id,
            "status": state["status"],
            "results": results,
        }

    def list_recons(self, limit: int = 20) -> list:
        """List recent recons, newest first."""
        recons = []
        if not self.recons_dir.exists():
            return recons

        for entry in self.recons_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("rec_"):
                continue
            try:
                state = self._load_state(entry)
                recons.append({
                    "recon_id": entry.name,
                    "query": state.get("query", ""),
                    "status": state.get("status", "unknown"),
                    "created_at": state.get("created_at", 0),
                    "agents": state.get("agents", []),
                })
            except (ReconError, json.JSONDecodeError):
                continue

        recons.sort(key=lambda r: r["created_at"], reverse=True)
        return recons[:limit]
