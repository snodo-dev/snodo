"""no_secrets_in_diff predicate — checks diff for credential leaks.

FILE: snodo/predicates/secrets.py (Task 7.8)
"""

import re
from typing import Any, Dict, List

from snodo.predicates.base import Predicate, PredicateContext, PredicateResult
from snodo.predicates.registry import _default_registry


# Default regex patterns for common credential leaks
_DEFAULT_PATTERNS: Dict[str, str] = {
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "generic_api_key": r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*[\"']?[\w\-_]{20,}[\"']?",
    "password_assignment": r"(?i)password\s*[:=]\s*[\"'][^\"']{4,}[\"']",
    "pem_header": r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
}


class NoSecretsInDiff(Predicate):
    """Scan git diff for credential or secret patterns in added lines.

    Params (from YAML constraint):
        patterns: Dict[str, str] — name → regex mapping (overrides defaults).

    Pre-execute: passes trivially (no task diff yet).
    Post-execute: scans added lines (+ prefix, excluding +++ headers).
    """

    def evaluate(self, context: PredicateContext, **params: Any) -> PredicateResult:
        if context.phase == "governance":
            return PredicateResult(
                passed=True,
                justification="Pre-execute: no diff to scan",
            )

        if context.git_mcp is None:
            return PredicateResult(
                passed=True,
                justification="No git MCP available to read diff",
            )

        patterns: Dict[str, str] = params.get("patterns") or _DEFAULT_PATTERNS

        try:
            diff_text: str = context.git_mcp.read_diff()
        except Exception as e:
            return PredicateResult(
                passed=True,
                justification=f"Could not read git diff: {e}",
            )

        if not diff_text.strip():
            return PredicateResult(
                passed=True,
                justification="No changes in working tree diff",
            )

        findings: List[Dict[str, Any]] = []
        for line_num, line in enumerate(diff_text.splitlines(), start=1):
            # Only check added lines (not removed and not diff headers)
            if not (line.startswith("+") and not line.startswith("+++")):
                continue
            stripped = line[1:]  # Remove leading '+'
            for pattern_name, pattern_re in patterns.items():
                for match in re.finditer(pattern_re, stripped):
                    findings.append({
                        "pattern": pattern_name,
                        "line": line_num,
                        "matched": match.group(),
                    })

        if not findings:
            return PredicateResult(
                passed=True,
                justification="No secrets or credentials detected in diff",
            )

        return PredicateResult(
            passed=False,
            justification=(
                f"Potential secrets detected in diff: "
                f"{len(findings)} match(es) across "
                f"{len(set(f['pattern'] for f in findings))} pattern(s)"
            ),
            evidence={"findings": findings},
        )


_default_registry.register("no_secrets_in_diff", NoSecretsInDiff())
