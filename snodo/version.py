"""Package version, including source-checkout identity when available.

``snodo`` is a PEP 420 namespace package (no top-level ``__init__.py``), so the
version lives in this module rather than on the package object. Import it as
``from snodo.version import __version__``.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
import tomllib


def _source_build_label(package_dir: Path) -> str | None:
    """Return ``b<count>.g<sha>`` for a git checkout, or ``None`` otherwise.

    Both git operations have strict timeouts so imports remain fast even when
    git is unavailable or a checkout has an unhealthy repository.
    """
    try:
        count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],  # noqa: S607 - git is the required repository tool
            cwd=package_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=0.5,
        ).stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git is the required repository tool
            cwd=package_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=0.5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not count.isdecimal() or not sha:
        return None
    return f"b{count}.g{sha}"


try:
    _base_version = version("snodo")
except PackageNotFoundError:
    _base_version = "unknown"


def _version_for(base_version: str, package_dir: Path) -> str:
    """Resolve the displayed version for a package directory."""
    build_label = _source_build_label(package_dir)
    if not build_label:
        return base_version

    try:
        project = tomllib.loads((package_dir.parent / "pyproject.toml").read_text())
        checkout_version = project["project"]["version"]
        if not isinstance(checkout_version, str):
            raise TypeError("project.version is not a string")
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        checkout_version = base_version
    return f"{checkout_version}+{build_label}"


__version__ = _version_for(_base_version, Path(__file__).resolve().parent)
