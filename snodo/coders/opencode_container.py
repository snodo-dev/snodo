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
_PORT = 8080


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
        """Start the opencode server container.

        The *workspace* directory is mounted at /workspace inside the
        container so opencode can read project files.

        Raises OpenCodeContainerError if the container is already
        running or startup fails.
        """
        if self._container is not None:
            raise OpenCodeContainerError("Container is already running")

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
