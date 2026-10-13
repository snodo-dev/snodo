"""Best-effort, cached check for a newer stable PyPI release."""

import json
import os
import time
from pathlib import Path
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version

from snodo.paths import resolve_home
from snodo.version import __version__

_PYPI_URL = "https://pypi.org/pypi/snodo/json"
_CACHE_FILE = "update-check.json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_REQUEST_TIMEOUT_SECONDS = 1.5


def get_newer_version(*, is_tty: bool) -> str | None:
    """Return a newer stable published version, or ``None`` on any issue.

    The check is intentionally opt-in at the call site through ``is_tty`` and
    never raises, so callers can safely include it in interactive UX.
    """
    if os.environ.get("SNODO_NO_UPDATE_CHECK") is not None:
        return None
    if os.environ.get("CI") is not None or not is_tty:
        return None

    try:
        installed = Version(__version__)
        cache_path = resolve_home() / _CACHE_FILE
        cached = _read_fresh_cache(cache_path)
        if cached is not _CACHE_MISS:
            return _newer_stable(cached, installed)

        with urlopen(_PYPI_URL, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            latest = json.loads(response.read().decode("utf-8"))["info"]["version"]
        latest_version = Version(latest)
        if latest_version.is_prerelease:
            latest = None
        _write_cache(cache_path, latest)
        return _newer_stable(latest, installed)
    except Exception:
        return None


_CACHE_MISS = object()


def _read_fresh_cache(path: Path) -> str | None | object:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checked_at = float(data["checked_at"])
        if time.time() - checked_at < _CACHE_TTL_SECONDS:
            version = data.get("version")
            if version is None or isinstance(version, str):
                return version
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return _CACHE_MISS


def _write_cache(path: Path, version: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"checked_at": time.time(), "version": version}),
        encoding="utf-8",
    )


def _newer_stable(version: str | None | object, installed: Version) -> str | None:
    if not isinstance(version, str):
        return None
    try:
        candidate = Version(version)
    except InvalidVersion:
        return None
    if candidate.is_prerelease or candidate <= installed:
        return None
    return version
