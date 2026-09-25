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
                                f"SSH exit code: {result.returncode}; "
                                + (result.stderr.strip() or result.stdout.strip())))
    except (OSError, subprocess.SubprocessError) as exc:
        checks.append(HostCheck("reachable", command, False, str(exc)))

    command = "snodo --version"
    try:
        result = _remote_command(host, command)
        actual = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        expected = f"snodo {__version__}"
        detail = f"expected {expected!r}; got {actual!r}; SSH exit code: {result.returncode}"
        remote_output = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())
        if remote_output:
            detail += f"; remote output: {remote_output}"
        if "snodo" in remote_output and (
            "command not found" in remote_output or "snodo: not found" in remote_output
        ):
            detail += "; snodo was not found; non-interactive SSH may not load the PATH that includes snodo"
        checks.append(HostCheck("version", command,
                                result.returncode == 0 and actual == expected,
                                detail))
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
                                f"expected remote {expected_remote!r}; got {actual_remote!r}; "
                                f"SSH exit code: {result.returncode}"
                                + (f"; remote stderr: {result.stderr.strip()}" if result.stderr.strip() else "")))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        checks.append(HostCheck("project_clone", command, False, str(exc)))
    return checks


def host_check_json(checks: list[HostCheck]) -> dict:
    """Serialize check results for stable CLI JSON output."""
    return {"ok": all(check.ok for check in checks), "checks": [asdict(check) for check in checks]}


def task_provider_models(protocol, coder_model: str, mode_id: str | None = None) -> dict[str, str]:
    """Return providers and representative models used by the task loop."""
    from snodo.config import ConfigManager

    manager = ConfigManager()
    config = manager.load()
    llm = config.get("llm", {})
    fallback = config.get("model") or coder_model
    mode = next((item for item in protocol.modes
                 if item.mode_id == (mode_id or protocol.initial_mode)), None)
    models = [coder_model]
    models.append(llm.get("validator", {}).get("model")
                  or llm.get("validator_llm", {}).get("model")
                  or fallback or coder_model)
    models.append(llm.get("classifier", {}).get("model")
                  or fallback or coder_model)
    if mode:
        active = set(mode.validators)
        for validator in protocol.validators:
            if validator.validator_id in active and validator.scope == "task":
                validator_model = getattr(validator, "model", None)
                if validator_model:
                    models.append(validator_model)
    providers: dict[str, str] = {}
    for model in models:
        provider = ConfigManager._provider_for_model(model)
        if provider:
            providers.setdefault(provider, model)
    return providers


def resolve_task_provider_keys(protocol, coder_model: str, mode_id: str | None = None):
    """Resolve only credentials required by this task loop, naming failures."""
    from snodo.config import ConfigManager

    manager = ConfigManager()
    keys: dict[str, str] = {}
    failures: list[HostCheck] = []
    for provider, model in task_provider_models(protocol, coder_model, mode_id).items():
        try:
            key = manager.get_key_for_model(model)
        except Exception:
            key = None
        if key:
            keys[provider] = key
            failures.append(HostCheck(f"provider_key:{provider}", "resolve locally", True,
                                      f"{provider} key resolves locally"))
        else:
            failures.append(HostCheck(f"provider_key:{provider}", "resolve locally", False,
                                      f"No locally resolvable key for provider '{provider}'"))
    return keys, failures
