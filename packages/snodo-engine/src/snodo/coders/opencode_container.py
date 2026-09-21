"""OpenCode Docker container lifecycle manager.

FILE: snodo/coders/opencode_container.py

Manages the opencode server container — start, stop, health check.
Built on docker-py.
"""

import logging
import io
import os
import tarfile
import time
import urllib.parse
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

_IMAGE = "snodo-opencode:latest"
_PORT = 55440
_DOCKER_REMEDIATION = "Install and start Docker: https://docs.docker.com/get-docker/"


class OpenCodeContainerError(Exception):
    """Container operation failed."""


class OpenCodeContainer:
    """Manages a long-lived opencode server container.

    The container runs ``opencode serve --port {port}`` inside the
    workspace directory and exposes the HTTP API on localhost.
    """

    def __init__(self, image: str = _IMAGE, port: int | None = None):
        self._image = image
        # The server's port inside the container is fixed by the image. A
        # missing host port is represented by Docker's atomic port allocation,
        # rather than a local scan that could describe the wrong daemon.
        self._port = port
        self._client = None
        self._container = None
        self._last_availability_reason = None

        docker_host = os.environ.get("DOCKER_HOST", "")
        if docker_host.startswith(("ssh://", "tcp://")):
            self._host = urllib.parse.urlparse(docker_host).hostname or "localhost"
        else:
            self._host = "localhost"

    @property
    def client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def is_available(self) -> bool:
        """Check whether Docker and the opencode image are usable."""
        return self.availability_reason() is None

    @property
    def last_availability_reason(self):
        """Return the diagnosis from the most recent availability probe."""
        return self._last_availability_reason

    def availability_reason(self):
        """Return an operator-facing reason if the container path is unavailable.

        This deliberately performs only the same cheap ping and image lookup
        used by the lifecycle manager; it does not start, build, or repair
        anything.
        """
        self._last_availability_reason = None
        try:
            self.client.ping()
        except Exception as e:
            _logger.debug("Docker ping failed (daemon unreachable?): %s: %s", type(e).__name__, e)
            if isinstance(e, (ModuleNotFoundError, FileNotFoundError)):
                reason = f"Docker is absent. {_DOCKER_REMEDIATION}"
            elif isinstance(e, ConnectionError):
                reason = (
                    f"Docker daemon is unreachable at {self._host}. "
                    f"Check the daemon, DOCKER_HOST, and SSH credentials. ({e})"
                )
            else:
                reason = (
                    f"Docker daemon check failed ({type(e).__name__}: {e}). "
                    f"{_DOCKER_REMEDIATION}"
                )
            self._last_availability_reason = reason
            return reason

        try:
            self.client.images.get(self._image)
        except Exception as e:
            _logger.debug("Image %s lookup failed: %s: %s", self._image, type(e).__name__, e)
            if type(e).__name__ == "ImageNotFound" or "no such image" in str(e).lower():
                reason = (
                    f"Docker image {self._image!r} is missing. "
                    f"Build it with 'docker build -t {self._image} -f docker/Dockerfile.opencode .'"
                )
            else:
                reason = (
                    f"Docker image {self._image!r} could not be checked "
                    f"({type(e).__name__}: {e}). Check the daemon and registry access."
                )
            self._last_availability_reason = reason
            return reason

        return None

    def image_exists(self) -> bool:
        """Check if the opencode image exists locally."""
        try:
            self.client.images.get(self._image)
            return True
        except Exception as e:
            _logger.debug("Image %s lookup failed: %s: %s", self._image, type(e).__name__, e)
            return False

    def build_image(self) -> str:
        """Build the opencode Docker image.

        Returns the image ID.

        Raises OpenCodeContainerError on failure.
        """
        dockerfile_dir = Path(__file__).parent.parent.parent / "docker"
        dockerfile_path = dockerfile_dir / "Dockerfile.opencode"
        if not dockerfile_path.exists():
            raise OpenCodeContainerError(
                f"Dockerfile not found at {dockerfile_path}"
            )
        try:
            image, _ = self.client.images.build(
                path=str(dockerfile_dir),
                dockerfile=dockerfile_path.name,
                tag=self._image,
                rm=True,
            )
            return image.id
        except Exception as e:
            raise OpenCodeContainerError(f"Failed to build image: {e}") from e

    def start(self, workspace: Path, task_id: Optional[str] = None) -> None:
        """Start the opencode server container owned by *task_id*.

        The *workspace* directory is mounted at /workspace inside the
        container so opencode can read project files.

        Raises OpenCodeContainerError if startup fails.
        """
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise OpenCodeContainerError(
                f"Workspace cannot be made available to the container: {workspace} "
                "is not a directory"
            )
        # If we already hold a reference and it's healthy, skip. This object
        # owns only the container it started or adopted for this task.
        if self._container is not None and self._is_container_healthy():
            return

        # A container is workspace-bound. An image match alone is never enough
        # to adopt one belonging to another task.
        existing = self._find_existing_container(task_id)
        if existing is not None:
            self._container = existing
            self._set_published_port()
            if self._is_container_healthy():
                self._copy_workspace_to_container(workspace)
                _logger.info("Reusing opencode container %s for task %s", existing.id[:12], task_id)
                return
            _logger.debug("Existing container %s is unhealthy — removing", existing.id[:12])
            self.stop()

        # Start fresh
        try:
            env = _build_provider_env()
            env["OPENCODE_PORT"] = str(_PORT)
            run_kwargs = {
                "detach": True,
                # The server's port inside the container is fixed by the
                # image; 0 asks Docker to allocate a free host port, on the
                # daemon, so concurrent tasks never contend for one.
                "ports": {
                    f"{_PORT}/tcp": self._port if self._port is not None else 0,
                },
                "publish_all_ports": False,
                "remove": True,
                "environment": env,
                "labels": {"com.snodo.task-id": task_id} if task_id else {},
            }
            if self.uses_workspace_mount:
                run_kwargs["volumes"] = {
                    str(workspace): {"bind": "/workspace", "mode": "rw"},
                }
            self._container = self.client.containers.run(
                self._image,
                **run_kwargs,
            )
            self._copy_workspace_to_container(workspace)
            self._port = self._published_port()
        except Exception as e:
            if self._container is not None:
                self.stop()
            self._container = None
            raise OpenCodeContainerError(f"Failed to start container: {e}") from e

        self._set_published_port()
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise

        _logger.info("Started new opencode container")
        self._log_readiness()

    @property
    def uses_workspace_mount(self) -> bool:
        """Whether the daemon shares the client's filesystem."""
        return self._host == "localhost"

    def sync_workspace_from_container(self, workspace: Path) -> None:
        """Copy the remote container workspace back to the client worktree."""
        if self.uses_workspace_mount:
            return
        if self._container is None:
            raise OpenCodeContainerError("Cannot read workspace: container is not running")

        try:
            stream, _ = self._container.get_archive("/workspace")
            archive = io.BytesIO(_archive_bytes(stream))
            with tarfile.open(fileobj=archive, mode="r:") as tar:
                members = tar.getmembers()
                _validate_archive_members(members)
                remote_files = {
                    Path(member.name).as_posix()
                    for member in members
                    if member.isfile()
                }
                for path in _workspace_files(workspace):
                    relative = path.relative_to(workspace).as_posix()
                    if relative not in remote_files:
                        path.unlink()
                tar.extractall(path=workspace, filter="data")
        except Exception as e:
            raise OpenCodeContainerError(
                f"Failed to copy workspace from remote Docker daemon: {e}"
            ) from e

    def _copy_workspace_to_container(self, workspace: Path) -> None:
        """Copy the client workspace into a remote container via Docker API."""
        if self.uses_workspace_mount:
            return
        try:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as tar:
                tar.add(workspace, arcname=".", recursive=True)
            self._container.put_archive("/workspace", buffer.getvalue())
        except Exception as e:
            raise OpenCodeContainerError(
                f"Failed to copy workspace to remote Docker daemon: {e}"
            ) from e

    def _find_existing_container(self, task_id: Optional[str] = None):
        """Return this task's running container, or None."""
        if not task_id:
            return None
        try:
            containers = self.client.containers.list(
                filters={
                    "ancestor": self._image,
                    "status": "running",
                    "label": f"com.snodo.task-id={task_id}",
                },
            )
            if containers:
                return containers[0]
        except Exception as e:
            _logger.debug("Failed to list running container by image: %s", e)
        return None
    def _published_port(self) -> int:
        """Return the host port Docker assigned to the container.

        Docker performs the allocation on the daemon, which may be remote.
        Reading the published binding after creation therefore avoids both a
        local-only availability check and a check-then-bind race.
        """
        try:
            self._container.reload()
            bindings = self._container.attrs["NetworkSettings"]["Ports"]
            binding = bindings[f"{_PORT}/tcp"][0]
            return int(binding["HostPort"])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            raise OpenCodeContainerError(
                f"Docker did not report the published port for {_PORT}/tcp: {e}"
            ) from e

    def _set_published_port(self) -> None:
        """Use Docker's assigned host port for the container HTTP endpoint."""
        if self._container is None:
            return
        try:
            self._container.reload()
            ports = self._container.attrs.get("NetworkSettings", {}).get("Ports", {})
            binding = ports.get(f"{_PORT}/tcp") or ports.get(f"{self._port}/tcp")
            if binding and binding[0].get("HostPort"):
                self._port = int(binding[0]["HostPort"])
        except Exception as e:
            _logger.debug("Could not resolve published opencode port: %s", e)

    def _is_container_healthy(self) -> bool:
        """Check if the container is running AND /global/health responds."""
        import httpx

        if not self.is_running():
            return False
        try:
            resp = httpx.get(
                f"{self.base_url}/global/health", timeout=2.0,
            )
            return resp.status_code == 200
        except Exception as e:
            # A health endpoint that refuses to answer because the HTTP
            # client broke is not the same as an unhealthy container;
            # keep the distinguishing reason.
            _logger.debug(
                "OpenCode health check request failed (%s/global/health): %s: %s",
                self.base_url, type(e).__name__, e,
            )
            return False

    def _wait_ready(self, timeout: float = 60.0) -> None:
        """Poll the opencode HTTP API until it responds or times out."""
        import httpx

        deadline = time.time() + timeout
        url = self.base_url + "/global/health"
        while time.time() < deadline:
            if self._container is None:
                raise OpenCodeContainerError("Container stopped during startup")
            try:
                resp = httpx.get(url, timeout=2.0)
                if resp.status_code < 500:
                    _logger.debug("OpenCode server ready at %s", self.base_url)
                    return
            except Exception as e:
                _logger.debug("OpenCode server health check poll error: %s", e)
            time.sleep(0.5)
        raise OpenCodeContainerError(
            f"OpenCode server did not become ready within {timeout}s"
        )

    def _log_readiness(self) -> None:
        """Log container health information at DEBUG level."""
        import httpx

        try:
            resp = httpx.get(
                f"{self.base_url}/global/health", timeout=2.0,
            )
            data = resp.json() if resp.status_code == 200 else {}
            version = data.get("version", "unknown")
            _logger.debug(
                "OpenCode container ready: http://%s:%d (v%s)",
                self._host, self._port, version,
            )
        except Exception:
            _logger.debug(
                "OpenCode container ready: http://%s:%d",
                self._host, self._port,
            )

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def is_running(self) -> bool:
        """Return True if the container is alive."""
        if self._container is None:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception as e:
            # reload() failing (daemon vanished, container deleted underneath
            # us) is reported as "not running"; the reason belongs in the log.
            _logger.debug("Container reload failed: %s: %s", type(e).__name__, e)
            return False

    def stop(self) -> None:
        """Stop and remove the container."""
        if self._container is not None:
            try:
                self._container.stop(timeout=5)
            except Exception as e:
                _logger.debug("Failed to stop opencode container: %s", e)
            try:
                self._container.remove(force=True)
            except Exception as e:
                _logger.debug("Failed to remove opencode container: %s", e)
            self._container = None


_PROVIDER_ENV_MAP = {
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "opencode": ("OPENCODE_API_KEY",),
}


def _build_provider_env() -> dict:
    """Read provider API keys from snodo config and return env vars for opencode."""
    env: dict[str, str] = {}
    try:
        from snodo.config import ConfigManager
        import os as _os
        config = ConfigManager().load()
        providers = config.get("providers", {})
        if not isinstance(providers, dict):
            return env
        for provider_name, env_var_names in _PROVIDER_ENV_MAP.items():
            provider_config = providers.get(provider_name, {})
            if not isinstance(provider_config, dict):
                continue
            key = provider_config.get("api_key", "")
            if not key:
                api_key_env_name = provider_config.get("api_key_env", "")
                if api_key_env_name:
                    key = _os.environ.get(api_key_env_name, "")
            if key:
                for env_name in env_var_names:
                    env[env_name] = key
    except Exception as e:
        _logger.warning("Failed to build provider env for opencode: %s", e)
    return env


def _archive_bytes(stream) -> bytes:
    """Read a Docker archive stream regardless of its concrete stream type."""
    if isinstance(stream, bytes):
        return stream
    return b"".join(stream)


def _workspace_files(workspace: Path):
    """Yield regular files in a workspace without following directory links."""
    return (
        path
        for path in workspace.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _validate_archive_members(members) -> None:
    """Reject archive paths that could write outside the task worktree."""
    for member in members:
        name = member.name
        if name.startswith("/") or ".." in Path(name).parts:
            raise OpenCodeContainerError(
                f"Remote workspace archive contains unsafe path: {name}"
            )
