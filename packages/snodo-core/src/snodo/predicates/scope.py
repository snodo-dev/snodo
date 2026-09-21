"""files_in_scope predicate — checks artifacts are within declared scope.

FILE: snodo/predicates/scope.py (Task 7.8)
"""

import fnmatch
from typing import Any, List, Optional

from snodo.predicates.base import Predicate, PredicateContext, PredicateResult
from snodo.predicates.registry import _default_registry


class FilesInScope(Predicate):
    """Verify all modified file paths fall within the task's effective scope.

    Params (from YAML constraint):
        scope_paths: List[str] — glob patterns for allowed paths.

    A task scoped to a module (ADR 041) is held to that module's declared
    paths instead of scope_paths: a write outside the module is a violation
    even when the protocol's scope covers the whole repository. A task with
    no module is judged against scope_paths exactly as before. This bounds
    writes — artifacts — only; reads are never narrowed by a module.

    Pre-execute: passes trivially (no artifacts yet).
    Post-execute: every artifact path must match at least one effective scope.
    """

    def evaluate(self, context: PredicateContext, **params: Any) -> PredicateResult:
        if context.phase == "governance" or not context.artifacts:
            return PredicateResult(
                passed=True,
                justification="Pre-execute: no artifacts to check",
            )

        module_id = getattr(context.task, "module_id", None)
        if module_id:
            return self._evaluate_against_module(context, module_id)

        scope_paths: List[str] = params.get("scope_paths", ["*"])
        out_of_scope = [
            path
            for path in context.artifacts
            if not any(fnmatch.fnmatch(path, p) for p in scope_paths)
        ]

        if not out_of_scope:
            return PredicateResult(
                passed=True,
                justification="All modified files are within declared scope",
            )

        return self._failure(out_of_scope, "declared scope")

    def _evaluate_against_module(
        self, context: PredicateContext, module_id: str
    ) -> PredicateResult:
        module = _find_module(context, module_id)
        if module is None:
            # The task names a bound the protocol does not declare. Resolving
            # it to the wider protocol scope would reintroduce exactly the
            # silent whole-repository pass this predicate exists to prevent,
            # so the check fails with the unresolved bound named.
            return PredicateResult(
                passed=False,
                justification=(
                    f"Task is scoped to module '{module_id}', which the "
                    "protocol does not declare; its bound cannot be resolved"
                ),
                evidence={"module_id": module_id},
            )

        out_of_scope = [
            path
            for path in context.artifacts
            if not _path_under_module(path, module.paths)
        ]

        if not out_of_scope:
            return PredicateResult(
                passed=True,
                justification=(
                    f"All modified files are within module '{module_id}'"
                ),
            )

        return self._failure(out_of_scope, f"module '{module_id}'")

    @staticmethod
    def _failure(out_of_scope: List[str], scope_label: str) -> PredicateResult:
        return PredicateResult(
            passed=False,
            justification=(
                f"Files outside {scope_label}: {', '.join(out_of_scope)}"
            ),
            evidence={"out_of_scope_files": out_of_scope},
        )


def _find_module(context: PredicateContext, module_id: str) -> Optional[Any]:
    """The protocol-declared module named by *module_id*, or None."""
    modules = getattr(context.protocol, "modules", None) or []
    for module in modules:
        if module.module_id == module_id:
            return module
    return None


def _path_under_module(path: str, module_paths: List[str]) -> bool:
    """Whether *path* is owned by a module declaring *module_paths*.

    A declared path matches as a glob (the fnmatch semantics scope_paths
    already use) and as a root directory — ``services/api`` owns everything
    beneath it — because modules declare root paths (ADR 041), which survey
    emits bare and operators may write with a ``/**`` suffix.
    """
    for declared in module_paths:
        if fnmatch.fnmatch(path, declared):
            return True
        root = declared.rstrip("/")
        if path == root or path.startswith(root + "/"):
            return True
    return False


_default_registry.register("files_in_scope", FilesInScope())
