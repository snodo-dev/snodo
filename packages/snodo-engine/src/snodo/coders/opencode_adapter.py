"""OpenCode coder adapter — delegates to opencode server in Docker.

FILE: snodo/coders/opencode_adapter.py

Implements CoderAdapter via the opencode HTTP API:
  POST /session       → session_id
  POST /session/{id}/message  → submit spec (model in message body)
  GET  /event          → SSE subscription for completion signal
  GET  /session/{id}/diff     → fetch changed files after completion

After the session completes, changes are read from the volume-mounted
workspace via git diff (the in-place edits are the source of truth).
The /diff API is used as a best-effort fallback.

The opencode server runs inside a Docker container managed by
OpenCodeContainer.  Each implement() call creates a fresh session.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

import httpx

from snodo.core.interfaces import TaskSpec, CodeArtifact, FileArtifact
from snodo.coders.base import CoderAdapter, LLMCallError

_logger = logging.getLogger(__name__)

_SESSION_TIMEOUT = 300.0  # 5 minutes


class OpenCodeAdapter(CoderAdapter):
    """Coder adapter backed by opencode CLI running in Docker."""

    skip_engine_commit: bool = True
    skip_workspace_write: bool = True

    def __init__(
        self,
        model: str = "opencode/",
        temperature: float = 0.7,
        workspace: Optional[Path] = None,
        container: Optional[Any] = None,
        workspace_mcp: Optional[Any] = None,
        **kwargs,
    ):
        self.model = model
        self.temperature = temperature

        if workspace is not None:
            self._workspace = workspace
        elif workspace_mcp is not None:
            from snodo.tools.workspace import WorkspaceMCP
            if isinstance(workspace_mcp, WorkspaceMCP):
                self._workspace = workspace_mcp.project_root
            else:
                self._workspace = Path.cwd()
        else:
            self._workspace = Path.cwd()

        if container is None:
            from snodo.coders.opencode_container import OpenCodeContainer
            self._container = OpenCodeContainer()
        else:
            self._container = container

    @property
    def base_url(self) -> str:
        return self._container.base_url

    def implement(self, spec: TaskSpec) -> CodeArtifact:
        """Generate code via the opencode HTTP API.

        1. Ensure container is running (start if needed)
        2. Create session via POST /session
        3. Subscribe to SSE /event BEFORE sending message (race-safe)
        4. Submit the task spec via POST /session/{id}/message (with model)
        5. Wait for session.idle event (threading.Event, 300s timeout)
        6. Read changed files from the volume-mounted workspace via git
        7. Fall back to GET /session/{id}/diff if git readback is empty
        8. Build CodeArtifact from on-disk file contents
        """
        if not self._container.is_running():
            self._start_container()

        session_id = self._create_session()
        try:
            self._wait_for_completion(session_id, spec)
            # Primary: read from the volume-mounted workspace (git diff)
            diff_entries = self._read_changes_from_disk()
            # Fallback: use the /diff API
            if not diff_entries:
                _logger.debug("git readback empty — trying /session/{id}/diff")
                diff_entries = self._fetch_diff(session_id)
            return self._diff_to_artifact(diff_entries)
        finally:
            self._cleanup_session(session_id)

    def _wait_for_completion(self, session_id: str, spec: TaskSpec) -> None:
        """Subscribe to SSE, send message, wait for session.idle event."""
        completed = threading.Event()

        def _listen_sse():
            try:
                with httpx.stream(
                    "GET",
                    f"{self.base_url}/event",
                    timeout=_SESSION_TIMEOUT,
                ) as r:
                    for line in r.iter_lines():
                        if completed.is_set():
                            break
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        props = event.get("properties", {}) or {}
                        event_sid = props.get("sessionID", "")
                        if event_sid != session_id:
                            continue
                        event_type = event.get("type", "")
                        if event_type == "session.idle":
                            completed.set()
                            return
                        if (
                            event_type == "session.status"
                            and (props.get("status", {}) or {}).get("type") == "idle"
                        ):
                            completed.set()
                            return
            except Exception:
                pass

        thread = threading.Thread(target=_listen_sse, daemon=True)
        thread.start()

        self._send_message(session_id, spec)

        if not completed.wait(timeout=_SESSION_TIMEOUT):
            raise LLMCallError(
                f"opencode session {session_id} timed out after {_SESSION_TIMEOUT}s"
            )

    def _fetch_diff(self, session_id: str) -> list:
        """GET /session/{id}/diff — return changed files as a list (fallback)."""
        diff_resp = httpx.get(
            f"{self.base_url}/session/{session_id}/diff",
            timeout=10.0,
        )
        data = diff_resp.json() if diff_resp.status_code == 200 else []
        _logger.debug(
            "opencode diff received: %d entries", len(data),
        )
        return data

    def _read_changes_from_disk(self) -> list:
        """Read changed files from the volume-mounted workspace via git diff.

        opencode edits files in-place through the Docker volume mount, so the
        on-disk state at ``self._workspace`` is the source of truth.  This
        method runs ``git diff`` to find what changed and returns entries in
        the same ``{file, status}`` format as ``_fetch_diff``.

        Returns:
            List of ``{file, status}`` dicts, or empty list on failure.
        """
        from git import Repo, GitCommandError

        try:
            repo = Repo(str(self._workspace), search_parent_directories=True)
        except (GitCommandError, Exception) as exc:
            _logger.warning("git readback: cannot open repo at %s: %s", self._workspace, exc)
            return []

        changed: dict[str, str] = {}

        try:
            # Unstaged changes (modified / deleted / added in working tree)
            for d in repo.index.diff(None):
                path = d.b_path or d.a_path
                if path:
                    if d.change_type == "D":
                        changed[path] = "deleted"
                    else:
                        changed[path] = d.change_type

            # Staged changes
            for d in repo.index.diff("HEAD"):
                path = d.b_path or d.a_path
                if path and path not in changed:
                    changed[path] = d.change_type

            # Untracked files (new files opencode created)
            for path in repo.untracked_files:
                changed[path] = "added"

        except Exception as exc:
            _logger.warning("git readback: diff failed: %s", exc)
            return []

        entries = []
        for path, status in changed.items():
            entries.append({"file": path, "status": status})

        _logger.debug("git readback: %d changed files", len(entries))
        return entries

    def _start_container(self) -> None:
        """Start the opencode container if not running."""
        from snodo.coders.opencode_container import OpenCodeContainerError

        if not self._container.is_available():
            raise LLMCallError(
                "Docker is not available. The opencode adapter requires Docker."
            )
        if not self._container.image_exists():
            _logger.info("Building opencode Docker image (first run)...")
            try:
                self._container.build_image()
            except OpenCodeContainerError as e:
                raise LLMCallError(f"Failed to build opencode image: {e}")

        try:
            self._container.start(self._workspace)
        except OpenCodeContainerError as e:
            raise LLMCallError(f"Failed to start opencode container: {e}")

    def _create_session(self) -> str:
        """POST /session — create a new session and return the ID."""
        try:
            resp = httpx.post(
                f"{self.base_url}/session",
                json={
                    "model": self._resolve_model_payload(),
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                raise LLMCallError(
                    f"opencode session creation failed (HTTP {resp.status_code}): "
                    f"{resp.text[:500]}"
                )
            data = resp.json()
            session_id = data.get("id") or data.get("session_id")
            if not session_id:
                raise LLMCallError(
                    f"opencode session response missing id: {resp.text[:500]}"
                )
            _logger.debug("opencode session created: %s", session_id)
            return session_id
        except (httpx.RequestError, json.JSONDecodeError) as e:
            raise LLMCallError(f"opencode session creation error: {e}")

    def _send_message(self, session_id: str, spec: TaskSpec) -> None:
        """POST /session/{id}/message — submit the task spec with model."""
        prompt = self._build_prompt(spec)
        try:
            resp = httpx.post(
                f"{self.base_url}/session/{session_id}/message",
                json={
                    "model": self._resolve_model_payload(),
                    "parts": [{"type": "text", "text": prompt}],
                },
                timeout=60.0,
            )
            if resp.status_code != 200:
                _logger.warning(
                    "opencode message rejected (HTTP %d): %s",
                    resp.status_code, resp.text[:500],
                )
                raise LLMCallError(
                    f"opencode message rejected (HTTP {resp.status_code})"
                )
        except httpx.RequestError as e:
            _logger.warning("opencode message send error: %s", e)

    def _cleanup_session(self, session_id: str) -> None:
        """Best-effort session cleanup."""
        try:
            httpx.delete(
                f"{self.base_url}/session/{session_id}",
                timeout=5.0,
            )
        except Exception:
            pass

    def _diff_to_artifact(self, diff_entries: list) -> CodeArtifact:
        """Convert opencode diff entries to a CodeArtifact.

        Each entry: ``{file, patch, additions, deletions, status}``.
        We need the full file content — re-read from disk after the
        opencode session has written changes.
        """
        files = []
        for entry in diff_entries:
            path = entry.get("file", "")
            if not path:
                continue
            status = entry.get("status", "modified")

            if status == "deleted":
                files.append(FileArtifact(path=path, content="", action="delete"))
                continue

            # Re-read the file from disk (opencode wrote to workspace)
            file_path = self._workspace / path
            try:
                content = file_path.read_text()
            except Exception:
                content = ""

            files.append(FileArtifact(path=path, content=content, action="write"))

        if not files:
            _logger.warning("opencode diff returned no files — task completed with no changes")
            return CodeArtifact(files=[])

        return CodeArtifact(files=files)

    def _resolve_model_payload(self) -> dict:
        """Map the snodo model string to an opencode model payload.

        opencode's API expects ``{"providerID": <p>, "modelID": <m>}``
        on ``POST /session/{id}/message``.
        """
        model = self.model
        if model.startswith("opencode/"):
            provider_and_id = model[len("opencode/"):]
            if "/" in provider_and_id:
                provider, model_id = provider_and_id.split("/", 1)
                return {"providerID": provider, "modelID": model_id}
            return {"modelID": provider_and_id}
        return {"modelID": model}

    def _build_prompt(self, spec: TaskSpec) -> str:
        """Build a prompt from the TaskSpec for opencode."""
        language = spec.project_context.get("language", "unknown")
        lang_hint = f" ({language} project)" if language != "unknown" else ""

        parts = [
            f"You are an expert software engineer{lang_hint}.",
            "Generate code based on the following specification.",
            "",
        ]

        structure = spec.project_context.get("structure", "")
        if structure:
            parts.append(f"## Directory Structure\n```\n{structure}\n```")
            parts.append("")

        if spec.memory_summary:
            parts.append(f"## Session History\n{spec.memory_summary}")
            parts.append("")

        parts.append(f"## Task\n{spec.description}")

        if spec.constraints:
            parts.append("\n## Constraints")
            for c in spec.constraints:
                parts.append(f"- {c}")

        parts.append("")
        parts.append(
            "Write the implementation to disk. Create all necessary files."
        )

        return "\n".join(parts)
