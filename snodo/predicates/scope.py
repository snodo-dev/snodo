"""files_in_scope predicate — checks artifacts are within declared scope.

FILE: snodo/predicates/scope.py (Task 7.8)
"""

import fnmatch
from typing import Any, List

from snodo.predicates.base import Predicate, PredicateContext, PredicateResult
from snodo.predicates.registry import _default_registry


class FilesInScope(Predicate):
    """Verify all modified file paths fall within declared scope globs.

    Params (from YAML constraint):
        scope_paths: List[str] — glob patterns for allowed paths.

    Pre-execute: passes trivially (no artifacts yet).
    Post-execute: every artifact path must match at least one scope_path.
    """

    def evaluate(self, context: PredicateContext, **params: Any) -> PredicateResult:
        if context.phase == "governance" or not context.artifacts:
            return PredicateResult(
                passed=True,
                justification="Pre-execute: no artifacts to check",
            )

        scope_paths: List[str] = params.get("scope_paths", ["*"])
        out_of_scope: List[str] = []

        for artifact_path in context.artifacts:
            if any(fnmatch.fnmatch(artifact_path, p) for p in scope_paths):
                continue
            out_of_scope.append(artifact_path)

        if not out_of_scope:
            return PredicateResult(
                passed=True,
                justification="All modified files are within declared scope",
            )

        return PredicateResult(
            passed=False,
            justification=(
                f"Files outside declared scope: {', '.join(out_of_scope)}"
            ),
            evidence={"out_of_scope_files": out_of_scope},
        )


_default_registry.register("files_in_scope", FilesInScope())
