"""Non-blocking findings for test and explanatory-text loss."""

import ast
import re
import logging
from pathlib import Path
from typing import List

from git import GitCommandError

from snodo.core.interfaces import ValidatorResult
from snodo.tools.git import open_repo

logger = logging.getLogger(__name__)


def _git_text(repo, ref: str, path: str) -> str:
    try:
        return repo.git.show(f"{ref}:{path}")
    except GitCommandError as exc:
        logger.warning("Could not read %s at revision %s: %s", path, ref, exc)
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
        repo = open_repo(root, search_parent_directories=True)
        names = repo.git.diff("--name-only", "--diff-filter=ACMRT", base_ref, head_ref).splitlines()
    except (GitCommandError, OSError) as exc:
        logger.warning("Could not inspect coverage changes from %s to %s: %s", base_ref, head_ref, exc)
        return []
    results = []
    for name in names:
        if not name.endswith(".py"):
            continue
        before = _git_text(repo, base_ref, name)
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
