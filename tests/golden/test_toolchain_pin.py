"""Verification toolchain is pinned exactly.

FILE: tests/golden/test_toolchain_pins.py

The tools a verification command invokes must be pinned to an exact version
so the gate cannot report differently per machine or per worktree. This
guard fails if any of them is declared with a range or without an upper
bound — an unbounded declaration must not reappear unnoticed.
"""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent


# (declaration, package, minimum acceptable pin)
GATE_TOOLS = [
    ("ruff", "ruff"),
    ("import-linter", "import-linter"),
    ("pytest", "pytest"),
    ("pytest-cov", "pytest-cov"),
    ("pytest-timeout", "pytest-timeout"),
    ("hypothesis", "hypothesis"),
    ("grimp", "grimp"),
    ("genbadge", "genbadge"),
    ("pytest-xdist", "pytest-xdist"),
]


def _parse_requirement(req: str):
    """Split 'name==1.2.3' / 'name[extra]>=1.2.3' into (name, specifier)."""
    req = req.strip()
    # strip extras
    if "[" in req:
        name, rest = req.split("[", 1)
        req = name + rest.split("]", 1)[1]
    # find the first operator
    for i, ch in enumerate(req):
        if ch in "=<>!~":
            return req[:i].strip(), req[i:].strip()
    return req, None


def _load_project() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


@pytest.fixture(scope="module")
def project() -> dict:
    return _load_project()


def _all_declared(project: dict) -> dict:
    """Map package name -> (raw declaration, specifier) across both dev lists."""
    declared: dict = {}
    for raw in project.get("project", {}).get("optional-dependencies", {}).get("dev", []):
        name, spec = _parse_requirement(raw)
        declared.setdefault(name, (raw, spec))
    for raw in project.get("dependency-groups", {}).get("dev", []):
        name, spec = _parse_requirement(raw)
        declared.setdefault(name, (raw, spec))
    return declared


@pytest.mark.parametrize("name,declared_name", GATE_TOOLS, ids=[d[1] for d in GATE_TOOLS])
def test_gate_tool_pinned_exactly(project, name, declared_name):
    """Each verification-tool declaration is pinned with '==', never a range."""
    declared = _all_declared(project)
    assert declared_name in declared, (
        f"gate tool '{name}' is not declared in pyproject.toml"
    )
    raw, spec = declared[declared_name]
    assert spec is not None and spec.startswith("=="), (
        f"{name} is not pinned exactly: '{raw}'. A gate whose behaviour "
        "depends on resolution order is not a gate — pin it with '=='."
    )


def test_gate_tool_versions_match_lockfile(project):
    """Each pinned gate must appear in uv.lock at the pinned version.

    The lockfile is the second half of the pin: a project pin with a stale
    lock lets an environment resolve something else.
    """
    lock_path = ROOT / "uv.lock"
    lock_text = lock_path.read_text()
    declared = _all_declared(project)

    for declared_name in dict(GATE_TOOLS).values():
        raw, spec = declared[declared_name]
        assert spec is not None and spec.startswith("==")
        version = spec[2:].strip()
        # find the [[package]] block for this name and check its version
        marker = f'name = "{declared_name}"'
        assert marker in lock_text, f"{declared_name} missing from uv.lock"
        # the version field immediately following the name line
        rest = lock_text.split(marker, 1)[1]
        version_line = rest.splitlines()[1]
        assert version_line.strip() == f'version = "{version}"', (
            f"uv.lock resolves {declared_name} {version_line.strip()!r}, "
            f"not the pinned {version}. Regenerate the lock (uv lock)."
        )
