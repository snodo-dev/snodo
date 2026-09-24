"""Remote execution host selection and ADR 055 preflight checks."""

import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from snodo.project import normalize_remote_url
from snodo.version import __version__


@dataclass(frozen=True)
class HostCheck:
    """Result of one named remote-host preflight check."""

    name: str
    command: str
    ok: bool
    detail: str = ""


def select_execution_host(execution: dict | object, environ: dict | None = None) -> str | None:
    """Resolve SNODO_HOST over execution.host; empty values select local."""
    env = os.environ if environ is None else environ
    configured = execution.get("host") if isinstance(execution, dict) else getattr(execution, "host", None)
    host = env.get("SNODO_HOST") or configured
    return host.strip() if isinstance(host, str) and host.strip() else None


def resolve_host_path(project_path: str | Path, host_path: str | None = None) -> str:
    """Return configured host path or this project's path relative to local home."""
    if host_path and host_path.strip():
        return host_path.strip()
    root = Path(project_path).resolve()
    try:
        relative = root.relative_to(Path.home().resolve())
    except ValueError as exc:
        raise ValueError(
            f"Cannot derive remote project path: {root} is not under local home; set execution.host_path"
        ) from exc
    return "~" if str(relative) == "." else f"~/{relative.as_posix()}"


def _remote_command(host: str, command: str, *, timeout: int = 10) -> subprocess.CompletedProcess:
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, command]
    return subprocess.run(  # noqa: S603 - ssh is an explicit operator-selected transport
        argv, capture_output=True, text=True, check=False, timeout=timeout + 5
    )


def _project_remote(project_path: str | Path) -> str:
    result = subprocess.run(  # noqa: S603, S607 - git resolved from PATH by design; argv-based invocation
        ["git",  # noqa: S607 - git resolved from PATH by design
         "-C", str(project_path), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"Local project has no origin remote: {result.stderr.strip()}")
    return result.stdout.strip()


def check_remote_host(
    host: str,
    project_path: str | Path = ".",
    host_path: str | None = None,
) -> list[HostCheck]:
    """Check reachability, exact snodo version, and matching remote clone.

    All checks are attempted independently so operators see every failure.
    """
    path = resolve_host_path(project_path, host_path)
    checks: list[HostCheck] = []

    command = "true"
    try:
        result = _remote_command(host, command)
        checks.append(HostCheck("reachable", command, result.returncode == 0,
                                result.stderr.strip() or result.stdout.strip()))
    except (OSError, subprocess.SubprocessError) as exc:
        checks.append(HostCheck("reachable", command, False, str(exc)))

    command = "snodo --version"
    try:
        result = _remote_command(host, command)
        actual = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        expected = f"snodo {__version__}"
        checks.append(HostCheck("version", command,
                                result.returncode == 0 and actual == expected,
                                f"expected {expected!r}; got {actual!r}"))
    except (OSError, subprocess.SubprocessError) as exc:
        checks.append(HostCheck("version", command, False, str(exc)))

    command = "git remote get-url origin"
    try:
        expected_remote = _project_remote(project_path)
        # Expanding a leading ~ in the remote shell supports the ADR's remote-home default.
        remote_path = '"$HOME"' + ("/" + shlex.quote(path[2:].lstrip("/")) if path.startswith("~/") else "")
        if path == "~":
            remote_path = '"$HOME"'
        elif not path.startswith("~/"):
            remote_path = shlex.quote(path)
        remote_root = f"{remote_path}"
        command = (
            f"test -d {remote_root}/.git && git -C {remote_root} rev-parse --is-inside-work-tree "
            f"&& git -C {remote_root} remote get-url origin"
        )
        result = _remote_command(host, command)
        lines = result.stdout.strip().splitlines()
        actual_remote = lines[-1].strip() if lines else ""
        is_clone = len(lines) >= 2 and lines[-2].strip() == "true"
        same_remote = bool(actual_remote) and normalize_remote_url(actual_remote) == normalize_remote_url(expected_remote)
        checks.append(HostCheck("project_clone", command,
                                result.returncode == 0 and is_clone and same_remote,
                                f"expected remote {expected_remote!r}; got {actual_remote!r}"))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        checks.append(HostCheck("project_clone", command, False, str(exc)))
    return checks


def host_check_json(checks: list[HostCheck]) -> dict:
    """Serialize check results for stable CLI JSON output."""
    return {"ok": all(check.ok for check in checks), "checks": [asdict(check) for check in checks]}
