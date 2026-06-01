"""Docker sandbox - Isolated container-based task execution.

FILE: snodo/sandbox/docker_sandbox.py (Task 5.4)

Uses docker-py to run tasks in isolated containers with:
- Workspace mount (rw)
- Network restrictions (default: none)
- Resource limits (memory, CPU)
- Configurable timeout
"""

import time
from pathlib import Path
from typing import Optional

from snodo.sandbox.base import Sandbox, SandboxConfig, SandboxResult, SandboxError


class DockerSandbox(Sandbox):
    """Docker-based sandbox for isolated task execution."""

    def __init__(self, image: str = "snodo-worker:latest", **kwargs):
        self._image = image
        self._client = None
        self._containers: list[str] = []

    @property
    def client(self):
        """Lazy-initialize Docker client."""
        if self._client is None:
            try:
                import docker
                self._client = docker.from_env()
            except ImportError:
                raise SandboxError(
                    "docker package not installed. Install with: pip install docker"
                )
            except Exception as e:
                raise SandboxError(f"Failed to connect to Docker: {e}")
        return self._client

    def is_available(self) -> bool:
        """Check if Docker daemon is reachable."""
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def image_exists(self) -> bool:
        """Check if the worker image exists locally."""
        try:
            self.client.images.get(self._image)
            return True
        except Exception:
            return False

    def _build_volumes(self, workspace: Path, config: SandboxConfig) -> dict:
        """Build volume mount mapping for the container."""
        volumes = {
            str(workspace.resolve()): {"bind": "/workspace", "mode": "rw"},
        }
        for host_path, mount_spec in config.mounts.items():
            volumes[host_path] = mount_spec
        return volumes

    def _collect_logs(self, container) -> tuple:
        """Collect stdout and stderr from a container.

        Returns:
            (stdout, stderr) tuple of decoded strings.
        """
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        return stdout, stderr

    def _remove_container(self, container) -> None:
        """Remove a container, ignoring errors."""
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass

    def run_task(
        self,
        command: list,
        workspace: Path,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxResult:
        """Run a task in a Docker container.

        Args:
            command: Command to run inside container
            workspace: Host workspace directory to mount
            config: Sandbox configuration

        Returns:
            SandboxResult with execution output

        Raises:
            SandboxError: If container creation or execution fails
        """
        if config is None:
            config = SandboxConfig(image=self._image)

        image = config.image or self._image
        volumes = self._build_volumes(workspace, config)
        environment = dict(config.env) if config.env else {}
        cpu_quota = int(config.cpu_limit * 100000) if config.cpu_limit else 0

        start_time = time.time()
        container = None

        try:
            container = self.client.containers.run(
                image,
                command=command,
                volumes=volumes,
                working_dir="/workspace",
                network_mode=config.network,
                detach=True,
                mem_limit=config.memory_limit,
                cpu_quota=cpu_quota,
                environment=environment,
                stderr=True,
                stdout=True,
            )
            self._containers.append(container.id)

            result = container.wait(timeout=config.timeout)
            stdout, stderr = self._collect_logs(container)

            return SandboxResult(
                exit_code=result.get("StatusCode", 1),
                stdout=stdout,
                stderr=stderr,
                duration=time.time() - start_time,
                sandbox_type="docker",
                container_id=container.id[:12],
            )

        except Exception as e:
            stdout, stderr = "", str(e)
            if container:
                try:
                    stdout, stderr = self._collect_logs(container)
                except Exception:
                    pass

            return SandboxResult(
                exit_code=1,
                stdout=stdout,
                stderr=stderr,
                duration=time.time() - start_time,
                sandbox_type="docker",
                container_id=container.id[:12] if container else None,
            )

        finally:
            self._remove_container(container)

    def build_image(self, dockerfile_path: Path, tag: Optional[str] = None) -> str:
        """Build the worker Docker image.

        Args:
            dockerfile_path: Path to Dockerfile
            tag: Image tag (defaults to self._image)

        Returns:
            Image ID

        Raises:
            SandboxError: If build fails
        """
        tag = tag or self._image
        build_path = str(dockerfile_path.parent)
        dockerfile_name = dockerfile_path.name

        try:
            image, build_logs = self.client.images.build(
                path=build_path,
                dockerfile=dockerfile_name,
                tag=tag,
                rm=True,
            )
            return image.id
        except Exception as e:
            raise SandboxError(f"Failed to build image: {e}")

    def cleanup(self) -> None:
        """Remove any leftover containers."""
        if self._client is None:
            return
        for container_id in self._containers:
            try:
                container = self._client.containers.get(container_id)
                container.remove(force=True)
            except Exception:
                pass
        self._containers.clear()
