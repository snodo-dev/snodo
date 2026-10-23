"""Non-blocking findings for test and explanatory-text loss."""

import ast
import re
import subprocess
from pathlib import Path
from typing import List

from snodo.core.interfaces import ValidatorResult


def _git_text(root: str, ref: str, path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True,
            text=True, check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except OSError:
        return ""


def _cases(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    return {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    }


def _explanation_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.lstrip().startswith(("#", '"""', "'''")))


def coverage_findings(*, base_ref: str, head_ref: str, task_spec: str, working_directory: str) -> List[ValidatorResult]:
    """Compare tracked Python test/doc content to base; never veto delivery."""
    root = str(Path(working_directory))
    try:
        names = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMRT", base_ref, head_ref],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []
    results = []
    for name in names:
        if not name.endswith(".py"):
            continue
        before = _git_text(root, base_ref, name)
        try:
            after = (Path(root) / name).read_text()
        except OSError:
            after = ""
        removed = _cases(before) - _cases(after)
        for case in sorted(removed):
            if re.search(rf"(?is).{{0,120}}{re.escape(case)}.{{0,120}}", task_spec):
                continue
            results.append(ValidatorResult(
                validator_id="coverage_accountability", severity="warn",
                justification=f"Test case `{case}` disappeared from `{name}`. Review whether its coverage is still provided.",
            ))
        old_count = _explanation_lines(before)
        new_count = _explanation_lines(after)
        if old_count >= 12 and old_count - new_count >= max(10, old_count // 2):
            if not re.search(r"(?is)(comment|docstring|documentation|explanation).{0,100}(remove|delete|shorten|rewrite)|(?:remove|delete|shorten|rewrite).{0,100}(comment|docstring|documentation|explanation)", task_spec):
                results.append(ValidatorResult(
                    validator_id="coverage_accountability", severity="warn",
                    justification=f"Explanatory comments/docstrings were substantially reduced in `{name}` ({old_count} lines to {new_count}). Review the removed contract explanation.",
                ))
    return results
