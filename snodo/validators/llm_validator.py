"""LLM Validator - AI-driven pre-execute validation.

FILE: snodo/validators/llm_validator.py (Task 6.1)

Uses the existing Coder adapter's LLM to evaluate tasks against
protocol-defined criteria before execution.

Judge prompt contract:
- Input: task spec + validator criteria from protocol YAML
- Output: JSON with {severity, justification}
- Falls back to "warn" on any LLM or parse failure
"""

import json
import re
from typing import Optional

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import _default_registry


class LLMValidator(ValidatorBase):
    """Evaluates tasks against protocol criteria using an LLM judge."""

    VALID_SEVERITIES = {"pass", "warn", "blocker"}

    HANDLED_TYPES = {
        "architecture", "security", "conventions",
        "performance", "testing", "planning",
    }

    def __init__(
        self,
        validator_spec: Validator,
        completion_fn=None,
        model: str = "gpt-4",
    ):
        self.validator_spec = validator_spec
        self._completion_fn = completion_fn
        self.model = model

    @classmethod
    def registered_type(cls) -> str:
        return "llm"

    def evaluate(self, context_or_task) -> ValidatorResult:
        # Backward-compat: accept Task for old test code
        if isinstance(context_or_task, Task):
            context = ValidatorContext(
                task=context_or_task,
                completion_fn=self._completion_fn,
                model=self.model,
            )
        else:
            context = context_or_task
            # Prefer context-provided values over instance defaults
            if context.completion_fn is not None:
                self._completion_fn = context.completion_fn
            if context.model:
                self.model = context.model

        prompt = self._build_prompt(context)

        try:
            response_text = self._call_llm(prompt)
            return self._parse_response(response_text)
        except Exception as e:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"LLM validation failed, defaulting to warn: {e}",
            )

    def _build_prompt(self, context_or_task) -> str:
        # Backward compat: accept Task directly for old test code
        if isinstance(context_or_task, Task):
            task = context_or_task
        else:
            task = context_or_task.task
        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )

        return (
            f"You are a {self.validator_spec.validator_type} validator for a software development protocol.\n"
            f"Evaluate the following task against the criteria below.\n"
            f"\n"
            f"## Task\n"
            f"{task.spec}\n"
            f"\n"
            f"## Criteria\n"
            f"{criteria_text}\n"
            f"\n"
            f"## Instructions\n"
            f"Evaluate the task against EACH criterion.\n"
            f"Return your evaluation as a JSON object with exactly two fields:\n"
            f"- \"severity\": one of \"pass\", \"warn\", or \"blocker\"\n"
            f"  - \"pass\" = all criteria satisfied\n"
            f"  - \"warn\" = minor concerns but can proceed\n"
            f"  - \"blocker\" = critical issues that must be addressed\n"
            f"- \"justification\": a brief explanation of your evaluation\n"
            f"\n"
            f"Respond with ONLY the JSON object, no other text.\n"
            f"\n"
            f"Example:\n"
            f'{{"severity": "pass", "justification": "Task meets all security criteria."}}\n'
        )

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM via the completion function.

        Args:
            prompt: The judge prompt

        Returns:
            Raw response text from the LLM

        Raises:
            Exception: If the LLM call fails
        """
        response = self._completion_fn(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=500,
        )
        return response.choices[0].message.content

    def _parse_response(self, response_text: str) -> ValidatorResult:
        """Parse LLM response into a ValidatorResult.

        Attempts JSON parsing, with fallback regex extraction.
        Falls back to "warn" if parsing fails entirely.

        Args:
            response_text: Raw LLM response text

        Returns:
            ValidatorResult with parsed severity and justification
        """
        # Try direct JSON parse first
        parsed = self._try_json_parse(response_text)

        if parsed is None:
            # Try extracting JSON from markdown code blocks or mixed text
            parsed = self._try_extract_json(response_text)

        if parsed is None:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"Could not parse LLM response: {response_text[:200]}",
            )

        severity = str(parsed.get("severity", "")).lower().strip()
        justification = str(parsed.get("justification", "No justification provided"))

        # Validate severity
        if severity not in self.VALID_SEVERITIES:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"Invalid severity '{severity}' from LLM. {justification}",
            )

        return ValidatorResult(
            validator_id=self.validator_spec.validator_id,
            severity=severity,
            justification=justification,
        )

    def _try_json_parse(self, text: str) -> Optional[dict]:
        """Try to parse text as JSON directly."""
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            return None

    def _try_extract_json(self, text: str) -> Optional[dict]:
        """Try to extract JSON from text with surrounding content."""
        # Try code block extraction
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # Try finding JSON object in text
        match = re.search(r'\{[^{}]*"severity"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

        return None


_default_registry.register_compound(LLMValidator.HANDLED_TYPES, LLMValidator)
