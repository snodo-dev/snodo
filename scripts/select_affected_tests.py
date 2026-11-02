#!/usr/bin/env python3
"""Print tests affected by the diff from a base commit to HEAD."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SHARED_FILES = {
    "pyproject.toml",
    "uv.lock",
    "tests/conftest.py",
    "tests/strategies.py",
    "Makefile",
    "scripts/gate_remote.sh",
    ".snodo/protocol.yml",
}


def _run_git(*args: str) -> str:
    return subprocess.check_output(  # noqa: S603 - git is the fixed executable
        ["git", *args], text=True, stderr=subprocess.PIPE  # noqa: S607
    ).strip()


def module_name(path: str) -> str | None:
    """Map a first-party Python source path to its importable dotted module."""
    parts = Path(path).with_suffix("").parts
    if "src" in parts:
        src_index = parts.index("src")
        if parts[src_index + 1 : src_index + 2] != ("snodo",):
            return None
        parts = parts[src_index + 1 :]
    elif parts and parts[0] == "snodo":
        pass
    else:
        return None
    if not parts or parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def test_references_module(source: str, module: str) -> bool:
    """Whether Python test source imports the given dotted module."""
    escaped = re.escape(module)
    # import a.b.c (including a comma-separated import list)
    if re.search(rf"(?m)^\s*import\s+[^\n#]*\b{escaped}\b", source):
        return True
    parent, _, leaf = module.rpartition(".")
    if parent and re.search(
        rf"(?m)^\s*from\s+{re.escape(parent)}\s+import\s+[^\n#]*\b{re.escape(leaf)}\b",
        source,
    ):
        return True
    # from a.b.c import ... is also an import of the module itself
    return bool(re.search(rf"(?m)^\s*from\s+{escaped}\s+import\b", source))


def select(repo: Path, base: str) -> list[str]:
    """Return sorted test paths affected by ``base..HEAD``."""
    _run_git("rev-parse", "--verify", f"{base}^{{commit}}")
    names = _run_git("diff", "--name-only", "--no-renames", base, "HEAD")
    changed = [name for name in names.splitlines() if name]
    if any(name in SHARED_FILES for name in changed):
        return ["tests/"]

    selected: set[str] = set()
    modules: set[str] = set()
    for name in changed:
        if name.startswith("tests/") and name.endswith(".py"):
            if (repo / name).is_file():
                selected.add(name)
        elif name.endswith(".py"):
            module = module_name(name)
            if module:
                modules.add(module)

    if modules:
        for path in sorted((repo / "tests").rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if any(test_references_module(source, module) for module in modules):
                selected.add(path.relative_to(repo).as_posix())
    return sorted(selected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Select tests affected by changes from a base commit to HEAD."
    )
    parser.add_argument("--base", required=True, help="Base commit or ref to compare.")
    args = parser.parse_args(argv)
    try:
        for path in select(Path.cwd(), args.base):
            print(path)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        print(f"select_affected_tests: invalid or unavailable base {args.base!r}: {detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
