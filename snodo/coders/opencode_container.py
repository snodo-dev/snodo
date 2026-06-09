"""OpenCode Docker container lifecycle manager.

FILE: snodo/coders/opencode_container.py

Manages the opencode server container — start, stop, health check.
Built on docker-py (same dependency as DockerSandbox).
"""

import logging
import time
from pathlib import Path

_logger = logging.getLogger(__name__)

_IMAGE = "snodo-opencode:latest"
_PORT = 55440


class OpenCodeContainerError(Exception):
    """Container operation failed."""


class OpenCodeContainer:
    """Manages a long-lived opencode server container.

    The container runs ``opencode serve --port {port}`` inside the
    workspace directory and exposes the HTTP API on localhost.
    """

    def __init__(self, image: str = _IMAGE, port: int = _PORT):
        self._image = image
        self._port = port
        self._client = None
        self._container = None

    @property
    def client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def is_available(self) -> bool:
        """Check if Docker daemon is reachable."""
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def image_exists(self) -> bool:
        """Check if the opencode image exists locally."""
        try:
            self.client.images.get(self._image)
            return True
        except Exception:
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
            raise OpenCodeContainerError(f"Failed to build image: {e}")

    def start(self, workspace: Path) -> None:
        """Start the opencode server container, reusing an existing one if healthy.

        The *workspace* directory is mounted at /workspace inside the
        container so opencode can read project files.

        Raises OpenCodeContainerError if startup fails.
        """
        # If we already hold a reference and it's healthy, skip
        if self._container is not None and self._is_container_healthy():
            return

        # Check for an existing container from a previous session
        existing = self._find_existing_container()
        if existing is not None:
            self._container = existing
            if self._is_container_healthy():
                _logger.info("Reusing existing opencode container %s", existing.id[:12])
                return
            _logger.debug("Existing container %s is unhealthy — removing", existing.id[:12])
            self.stop()

        # Start fresh
        try:
            volumes = {
                str(workspace.resolve()): {"bind": "/workspace", "mode": "rw"},
            }
            self._container = self.client.containers.run(
                self._image,
                detach=True,
                volumes=volumes,
                ports={f"{self._port}/tcp": self._port},
                publish_all_ports=False,
                remove=True,
                environment={
                    "OPENCODE_PORT": str(self._port),
                },
            )
        except Exception as e:
            self._container = None
            raise OpenCodeContainerError(f"Failed to start container: {e}")

        self._wait_ready()

        _logger.info("Started new opencode container")
        self._log_readiness()

    def _find_existing_container(self):
        """Return an existing running container with this image, or None."""
        try:
            containers = self.client.containers.list(
                filters={"ancestor": self._image, "status": "running"},
            )
            if containers:
                return containers[0]
        except Exception:
            pass
        return None

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
        except Exception:
            return False

    def _wait_ready(self, timeout: float = 30.0) -> None:
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
            except Exception:
                pass
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
                "OpenCode container ready: http://localhost:%d (v%s)",
                self._port, version,
            )
        except Exception:
            _logger.debug(
                "OpenCode container ready: http://localhost:%d",
                self._port,
            )

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self._port}"

    def is_running(self) -> bool:
        """Return True if the container is alive."""
        if self._container is None:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def stop(self) -> None:
        """Stop and remove the container."""
        if self._container is not None:
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass
            try:
                self._container.remove(force=True)
            except Exception:
                pass
            self._container = None
