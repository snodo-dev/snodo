"""Regression guard: the completion-binding rule has exactly one implementation.

FILE: tests/infrastructure/test_completion_binding_single_impl.py

Fixes #256.

The rule: the name bound to a litellm completion must be the provider
block's litellm routing name, and its api_base must be resolved alongside
it.  It was written three times — engine/loop._build_completion_fn,
validators/runner.build_completion_fn, and (by omission) the two
protocol_adherence call sites — and two copies disagreed on one line, so
one surface worked and the other failed against a gateway provider.

This test walks the source trees (not just one file) and asserts that the
factory which resolves the routing model and api_base and returns a
``functools.partial`` appears in exactly one place:
``snodo/validators/runner.py``.  A fourth copy added next month — under any
name, in any package — fails here.
"""

import ast
from pathlib import Path

# The one module allowed to implement the rule.
_CANONICAL = "packages/snodo-engine/src/snodo/validators/runner.py"
_CANONICAL_FUNC = "build_completion_fn"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOTS = [_PROJECT_ROOT / "snodo"] + sorted(
    (_PROJECT_ROOT / "packages").glob("*/src/snodo")
)


def _call_name(node: ast.Call) -> str:
    """Return the bare/attribute name of a call, e.g. ``functools.partial``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _implementations_in(file_path: Path) -> list:
    """Return function names in *file_path* that implement the binding rule.

    A function implements the rule when it both resolves the litellm routing
    model and the provider api_base, and returns a bound ``partial``.
    """
    try:
        tree = ast.parse(file_path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        names = {_call_name(c) for c in calls}
        if (
            "resolve_litellm_model" in names
            and "resolve_api_base" in names
            and "partial" in names
        ):
            found.append(node.name)
    return found


def _all_implementations() -> list:
    """Collect (relative path, function name) for every rule implementation."""
    implementations = []
    for root in _SOURCE_ROOTS:
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            for func in _implementations_in(py_file):
                rel = str(py_file.relative_to(_PROJECT_ROOT))
                implementations.append((rel, func))
    return implementations


def test_completion_binding_rule_has_single_implementation():
    """Only runner.build_completion_fn implements the binding rule."""
    implementations = _all_implementations()
    expected = [(_CANONICAL, _CANONICAL_FUNC)]
    assert implementations == expected, (
        "THE COMPLETION-BINDING RULE HAS MORE THAN ONE HOME:\n"
        + "\n".join(f"  {path}: {func}" for path, func in implementations)
        + f"\n\nExpected exactly {expected}. "
        "A second copy drifts: it is how validate_task over MCP failed "
        "against a gateway provider while the CLI path worked (Fixes #256). "
        "Route every caller through the one implementation instead."
    )


def test_no_legacy_build_completion_fn_definition_outside_runner():
    """No module defines a second build_completion_fn / _build_completion_fn."""
    offenders = []
    for root in _SOURCE_ROOTS:
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(py_file.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        node.name in ("build_completion_fn", "_build_completion_fn"):
                    rel = str(py_file.relative_to(_PROJECT_ROOT))
                    if rel != _CANONICAL:
                        offenders.append((rel, node.name))
    assert offenders == [], (
        "A SECOND build_completion_fn DEFINITION EXISTS:\n"
        + "\n".join(f"  {path}: {func}" for path, func in offenders)
        + "\n\nImport snodo.validators.runner.build_completion_fn instead "
        "of forking the rule (Fixes #256)."
    )
