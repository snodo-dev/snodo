"""tests_exist_for_modified predicate — checks test files exist for modified code.

FILE: snodo/predicates/tests.py (Task 7.8)
"""

import os
from typing import Any, List

from snodo.predicates.base import Predicate, PredicateContext, PredicateResult
from snodo.predicates.registry import _default_registry


class TestsExistForModified(Predicate):
    """Verify that test files exist for modified implementation files.

    Params (from YAML constraint):
        test_dir_pattern: str = "tests/" — directory containing tests
        test_name_pattern: str = "test_{stem}.py" — naming convention

    Pre-execute: passes trivially (no artifacts yet).
    Post-execute: for each non-test modified file, check a corresponding
    test file exists per the naming pattern.
    """

    def evaluate(self, context: PredicateContext, **params: Any) -> PredicateResult:
        if context.phase == "governance" or not context.artifacts:
            return PredicateResult(
                passed=True,
                justification="Pre-execute: no artifacts to check",
            )

        test_dir_pattern: str = params.get("test_dir_pattern", "tests/")
        test_name_pattern: str = params.get("test_name_pattern", "test_{stem}.py")
        missing_tests: List[str] = []

        for artifact_path in context.artifacts:
            # Skip artifacts that are already test files
            if artifact_path.startswith(test_dir_pattern):
                continue
            # Skip non-code artifacts (e.g. git_commit markers)
            stem = os.path.splitext(os.path.basename(artifact_path))[0]
            if not stem:
                continue

            test_path = os.path.join(
                test_dir_pattern,
                test_name_pattern.format(stem=stem, path=artifact_path),
            )

            if context.workspace_mcp is not None:
                try:
                    if not context.workspace_mcp.file_exists(test_path):
                        missing_tests.append(test_path)
                except Exception:
                    missing_tests.append(test_path)
            else:
                # No workspace available — cannot verify
                return PredicateResult(
                    passed=True,
                    justification="No workspace MCP available to verify test files",
                )

        if not missing_tests:
            return PredicateResult(
                passed=True,
                justification="All modified files have corresponding tests",
            )

        return PredicateResult(
            passed=False,
            justification=(
                f"Modified files without tests: {', '.join(missing_tests)}"
            ),
            evidence={"missing_tests": missing_tests},
        )


_default_registry.register("tests_exist_for_modified", TestsExistForModified())
