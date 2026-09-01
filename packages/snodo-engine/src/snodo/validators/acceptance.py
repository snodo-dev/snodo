"""Acceptance validator — judges produced artifacts against the task's
acceptance criteria.

FILE: snodo/validators/acceptance.py (Fixes #54)

The pipeline verifies that the repository still works (quality runs the test
suite) but nothing verifies that the task was carried out.  A coder that does
part of the job — no test for the new feature, no ADR recording a decision the
code now contradicts — passes every validator and auto-merges.

This validator runs post-execute and judges the produced artifacts against the
acceptance criteria in the task spec.  It is a completeness check against the
spec, not a correctness check of the code (that is ``quality``'s job).

Design (ADR 028):

- **Warn, not blocker.** "You forgot the test" is exactly the kind of fault a
  coder can fix given the feedback, so a miss routes to recovery (warn) rather
  than a hard halt.  The validator is shipped with ``severity_cap: warn`` so a
  miss can never hard-block even if a protocol forgets the cap.
- **"Unmet" is distinct from "uncheckable".** A criterion that cannot be
  verified from the tree (device behaviour, human judgement, a decision only a
  human can make) is reported as uncheckable and does NOT block good work.  The
  judge is told to return ``pass`` for uncheckable criteria and to say so in
  the justification; only criteria that are verifiable from the tree and
  demonstrably unmet produce a warn.
- **Not a second ``quality``.** It judges completeness against the spec, not
  correctness of the code.  It never runs commands; it reads the tree.
"""

from typing import Set

from snodo.compiler.models import Validator
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator
from snodo.validators.registry import _default_registry


class AcceptanceValidator(LLMValidator):
    """Post-execute validator that judges artifacts against acceptance criteria.

    Reuses the LLMValidator tool loop unchanged; only the judge prompt differs.
    """

    def __init__(
        self,
        validator_spec: Validator,
        completion_fn=None,
        model: str = "",
    ):
        super().__init__(validator_spec, completion_fn, model)

    @classmethod
    def registered_type(cls) -> str:
        return "acceptance"

    def _build_tool_loop_prompt(
        self,
        context: ValidatorContext,
        active_names: Set[str],
        has_diff: bool,
        change_diff: str,
        diff_label: str = "",
        diff_is_fallback: bool = False,
    ) -> str:
        """Judge the produced artifacts against the task's acceptance criteria.

        The task spec is the source of the acceptance criteria.  The judge is
        told to distinguish "unmet" (verifiable from the tree and demonstrably
        absent) from "uncheckable" (device behaviour, human judgement — not
        verifiable from the tree, and never a finding).
        """
        artifacts = list(getattr(context, "artifacts", None) or [])
        artifact_text = "\n".join(f"  - {a}" for a in artifacts) or "  (none)"

        prompt_parts = [
            "You are an acceptance validator for a software development protocol.\n",
            "The task below was carried out and produced artifacts.  Judge the "
            "produced artifacts against the task's ACCEPTANCE CRITERIA.\n",
            "\n",
            "## Phase\n",
            "You are inspecting COMPLETED work. The described change has been "
            "implemented; judge the finished result. Absence of the described "
            "work, or of the tests and tooling it requires, IS a finding.\n",
            "\n",
            "## Task\n",
            f"{context.task.spec}\n",
            "\n",
            "## Produced Artifacts\n",
            f"{artifact_text}\n",
        ]

        if has_diff and change_diff:
            label = diff_label or "HEAD~1..HEAD"
            parts = [
                "\n",
                f"## Code Change ({label})\n",
                f"```\n{change_diff}\n```\n",
            ]
            if diff_is_fallback:
                parts.append(
                    "NOTE: this diff was read against HEAD~1..HEAD because no "
                    "execute-node HEAD anchor was available — it may show the "
                    "previous commit rather than this task's produced change.\n"
                )
            prompt_parts.extend(parts)

        mutations = []
        if hasattr(context, "code_artifact") and getattr(context.code_artifact, "metadata", None):
            mutations = context.code_artifact.metadata.get("test_governing_mutations", [])
        elif hasattr(context, "metadata") and isinstance(context.metadata, dict):
            mutations = context.metadata.get("test_governing_mutations", [])

        if mutations:
            mutation_lines = [f"  - {m['path']} ({m['kind']})" for m in mutations if isinstance(m, dict)]
            prompt_parts.extend([
                "\n",
                "## Test-Governing File Modifications Detected (ADR 040)\n",
                "The coder modified or deleted test-governing files during this task:\n",
                "\n".join(mutation_lines),
                "\n\n",
                "ATTENTION: Evaluate whether these test-governing file changes were explicitly authorized by "
                "the task specification or if they represent unauthorized test weakening or deletion. If a test file "
                "was modified or deleted to suppress failures without spec authorization, report this as UNMET with severity='warn'.\n",
            ])

        prompt_parts.extend([
            "\n",
            "## Available Tools\n",
            "You may call read-only tools to inspect files and git history.\n",
            "When you are ready to deliver your verdict, call the\n",
            "`submit_verdict(severity, justification)` tool — this is the\n",
            "ONLY way to return your verdict.  Do NOT narrate your verdict\n",
            "as prose; use the tool.\n",
            "\n",
            "## Instructions\n",
            "Identify the acceptance criteria in the task spec (look for "
            "explicit 'acceptance criteria', 'done when', 'must', or a "
            "numbered list of requirements).  For EACH criterion, decide:\n",
            "- MET: the produced artifacts satisfy it. Evidence exists in the tree.\n",
            "- UNMET: the criterion is verifiable from the tree and the produced "
            "artifacts demonstrably do not satisfy it. This is a finding.\n",
            "- UNCHECKABLE: the criterion cannot be verified from static tree inspection "
            "alone (command/suite execution such as running verification tools; "
            "device behaviour; human judgement; performance under load). This is NEVER a finding — "
            "return pass for it and say it is uncheckable. You do NOT have shell tools to "
            "run commands. NEVER mark a command execution criterion as MET by inferring success "
            "from static files — mark it UNCHECKABLE, return pass for it, and state that command "
            "execution is uncheckable by read-only judges.\n",
            "\n",
            "A criterion that is verifiable from the tree and unmet is a WARN, "
            "never a blocker.  If every criterion is met or uncheckable, return "
            "pass.  If the task spec has no acceptance criteria, return pass "
            "and say so.\n",
            "\n",
            "Use tools to read files if needed.  Then call submit_verdict with "
            "severity in [\"pass\", \"warn\", \"blocker\"] and a concise "
            "justification naming each unmet criterion and the evidence.\n",
        ])

        return "".join(prompt_parts)


_default_registry.register(AcceptanceValidator.registered_type(), AcceptanceValidator)
