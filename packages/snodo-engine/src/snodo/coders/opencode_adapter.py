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
from snodo.coders.base import CoderUnavailableError, InPlaceCoderAdapter, LLMCallError

_logger = logging.getLogger(__name__)

_SESSION_TIMEOUT = 300.0  # 5 minutes

_DOCKER_REMEDIATION = "Install and start Docker: https://docs.docker.com/get-docker/"


class OpenCodeAdapter(InPlaceCoderAdapter):
    """Coder adapter backed by opencode CLI running in Docker.

    Writes to the volume-mounted workspace in place (never through
    WorkspaceMCP), so the .snodo/ boundary is enforced by the base class:
    a post-run .snodo/ mutation raises ``SnodoMutationError`` instead of
    being filtered out of the artifact report (Fixes #52, ADR 027).
    """

    coder_name: str = "opencode"
    skip_engine_commit: bool = True
    skip_workspace_write: bool = True

    #: The container coder reads the model and its workspace/container. It does
    #: NOT honour ``timeout_seconds`` — the
    #: session budget is the hardcoded ``_SESSION_TIMEOUT`` below, and the
    #: value from config is absorbed by ``**kwargs`` and dropped — nor
    #: ``temperature``, which the constructor stores and nothing ever reads,
    #: nor ``max_tokens``/``max_tool_turns``, which belong to a loop this
    #: adapter does not run (Fixes #311).
    honoured_settings: frozenset[str] = frozenset(
        {"model", "workspace", "container"}
    )

    @classmethod
    def availability_requirements(cls) -> tuple:
        """The container coder needs a Docker runtime, not a host CLI."""
        return (("docker", _DOCKER_REMEDIATION),)

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

    def _implement_in_place(self, spec: TaskSpec) -> CodeArtifact:
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

        session_id = None
        try:
            self._validate_model_available()
            session_id = self._create_session()
            self._wait_for_completion(session_id, spec)
            try:
                self._container.sync_workspace_from_container(self._workspace)
            except Exception as e:
                raise LLMCallError(f"Failed to retrieve opencode workspace: {e}") from e
            # Primary: read from the volume-mounted workspace (git diff)
            diff_entries = self._read_changes_from_disk()
            # Fallback: use the /diff API
            if not diff_entries:
                _logger.debug("git readback empty — trying /session/{id}/diff")
                diff_entries = self._fetch_diff(session_id)
            return self._diff_to_artifact(diff_entries)
        finally:
            if session_id is not None:
                self._cleanup_session(session_id)
            # Containers are task-scoped resources, including when execution
            # raises or the task is halted.
            self._container.stop()

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
            except Exception as e:
                _logger.debug("opencode SSE listener error: %s", e)

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

    def _start_container(self) -> None:
        """Start the opencode container if not running."""
        from snodo.coders.opencode_container import OpenCodeContainerError

        if not self._container.is_available():
            # The probe records the concrete operator action (daemon, remote
            # connection, or image) without trying to repair the environment.
            reason = getattr(self._container, "last_availability_reason", None)
            if not isinstance(reason, str) or not reason:
                reason = _DOCKER_REMEDIATION
            raise CoderUnavailableError("docker", reason)

        try:
            self._container.start(
                self._workspace,
                task_id=getattr(self, "_task_id", None),
            )
        except OpenCodeContainerError as e:
            raise LLMCallError(f"Failed to start opencode container: {e}") from e

    def _create_session(self) -> str:
        """POST /session — create a new session and return the ID."""
        try:
            resp = httpx.post(
                f"{self.base_url}/session",
                json={},
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
            raise LLMCallError(f"opencode session creation error: {e}") from e

    def _validate_model_available(self) -> None:
        """Refuse to dispatch a model the running server does not provide."""
        try:
            resp = httpx.get(
                f"{self.base_url}/config/providers",
                timeout=10.0,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            available = self._available_models(resp.json())
        except (httpx.RequestError, json.JSONDecodeError, RuntimeError) as e:
            raise CoderUnavailableError(
                "opencode model",
                message=(
                    f"Could not verify requested opencode model '{self.model}': {e}"
                ),
            ) from e

        payload = self._resolve_model_payload()
        requested = (payload.get("providerID"), payload["modelID"])
        if requested in available or (
            requested[0] is None
            and any(model_id == requested[1] for _, model_id in available)
        ):
            return

        available_names = ", ".join(
            f"{provider}/{model_id}" if provider else model_id
            for provider, model_id in sorted(
                available, key=lambda item: (item[0] or "", item[1])
            )
        ) or "none"
        raise CoderUnavailableError(
            "opencode model",
            message=(
                f"Requested opencode model '{self.model}' is not available; "
                f"server provides: {available_names}"
            ),
        )

    @staticmethod
    def _available_models(data: Any) -> set[tuple[Optional[str], str]]:
        """Extract provider/model pairs from the /config/providers response."""
        providers = data.get("providers", data) if isinstance(data, dict) else data
        available: set[tuple[Optional[str], str]] = set()

        if isinstance(providers, list):
            provider_items = (
                (item.get("id"), item)
                for item in providers
                if isinstance(item, dict)
            )
        elif isinstance(providers, dict):
            provider_items = providers.items()
        else:
            return available

        for provider_id, provider in provider_items:
            if not isinstance(provider, dict):
                continue
            models = provider.get("models", {})
            if isinstance(models, dict):
                model_items = models.items()
            elif isinstance(models, list):
                model_items = (
                    (item.get("id"), item)
                    for item in models
                    if isinstance(item, dict)
                )
            else:
                continue
            for model_id, model in model_items:
                if not model_id and isinstance(model, dict):
                    model_id = model.get("id")
                if isinstance(model_id, str) and model_id:
                    available.add(
                        (provider_id if isinstance(provider_id, str) else None, model_id)
                    )
        return available

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
        except Exception as e:
            _logger.debug("opencode session cleanup error for %s: %s", session_id, e)

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
            file_path = Path(self._workspace) / path
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
