"""Protocol-adherence validator — meta-validator for mode-boundary enforcement.

FILE: snodo/validators/protocol_adherence.py (Task 7.11)

Implements the paper's Section 4.10 claim: meta-validators that police
the protocol's own rules without special-case language constructs.

This validator checks whether a submitted task spec describes work
appropriate to the current mode by reasoning over the mode's
operational profile (derived from protocol primitives — tools,
validators, transitions — not from declared text).
"""

import json
import re
from typing import Any, Dict, Optional

from litellm import supports_response_schema

from snodo.compiler.models import Validator
from snodo.core.interfaces import ValidatorResult
from snodo.validators.context import ValidatorContext, ValidatorBase
from snodo.validators.registry import _default_registry
from snodo.infrastructure.config import DEFAULT_MODEL


_DEFAULT_MAX_TOKENS = 1500


class ProtocolAdherenceValidator(ValidatorBase):
    """Validates task-to-mode semantic alignment."""

    VALID_SEVERITIES = {"pass", "warn", "blocker"}

    def __init__(
        self,
        validator_spec: Validator,
        completion_fn=None,
        model: str = DEFAULT_MODEL,
    ):
        self.validator_spec = validator_spec
        self._completion_fn = completion_fn
        self.model = model
        self.completion_tokens = _DEFAULT_MAX_TOKENS

    @classmethod
    def registered_type(cls) -> str:
        return "protocol"

    def evaluate(self, context: ValidatorContext) -> ValidatorResult:
        """Evaluate task spec against current mode profile.

        Args:
            context: ValidatorContext with task, mode, protocol

        Returns:
            ValidatorResult with severity and justification.
            Falls back to "warn" on LLM or parse failure.
        """
        # Prefer context-provided values over instance defaults
        if context.completion_fn is not None:
            self._completion_fn = context.completion_fn
        if context.model:
            self.model = context.model
        ctx_tokens = getattr(context, "max_tokens", None)
        if ctx_tokens is not None:
            self.completion_tokens = ctx_tokens

        prompt = self._build_prompt(context)

        # Try structured output when the model supports it
        if self._completion_fn is not None and supports_response_schema(self.model):
            try:
                return self._call_llm_structured(prompt)
            except Exception:
                pass  # fall through to legacy parse

        # Legacy: free-text completion + hand-rolled parse
        try:
            response_text = self._call_llm(prompt)
            return self._parse_response(response_text)
        except Exception as e:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"Protocol-adherence LLM validation failed, defaulting to warn: {e}",
            )

    # ------------------------------------------------------------------
    # Mode profile derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_mode_profile(mode: Any) -> Dict[str, Any]:
        """Build structured profile from a Mode object."""
        profile: Dict[str, Any] = {
            "mode_id": mode.mode_id,
            "mode_name": mode.name,
            "tools": list(mode.tools),
            "applied_validators": [],
            "transitions": dict(mode.transitions),
        }
        # Resolve validator refs to (validator_id, validator_type) pairs
        # The mode object has a back-reference to its protocol available
        # through context.protocol; we use that in _build_prompt.
        return profile

    def _enrich_profile(
        self,
        profile: Dict[str, Any],
        protocol: Any,
    ) -> Dict[str, Any]:
        """Add resolved validator details to a mode profile."""
        resolved = []
        for vid in getattr(protocol.get_mode(profile["mode_id"]), "validators", []):
            v = protocol.get_validator(vid)
            if v is not None:
                resolved.append({
                    "validator_id": v.validator_id,
                    "validator_type": v.validator_type,
                })
        profile["applied_validators"] = resolved
        return profile

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, context: ValidatorContext) -> str:
        """Build LLM prompt with mode profiles and task spec."""
        protocol = context.protocol
        current_mode = context.current_mode

        # Derive current mode profile
        current_profile = self._derive_mode_profile(current_mode)
        current_profile = self._enrich_profile(current_profile, protocol)

        # Derive sibling mode profiles
        sibling_profiles = []
        for sibling in protocol.modes:
            if sibling.mode_id != current_mode.mode_id:
                profile = self._derive_mode_profile(sibling)
                profile = self._enrich_profile(profile, protocol)
                sibling_profiles.append(profile)

        criteria_text = "\n".join(
            f"  {i+1}. {c}" for i, c in enumerate(self.validator_spec.criteria)
        )

        # Format current profile
        current_section = self._format_profile(current_profile)

        # Format siblings
        siblings_section = ""
        if sibling_profiles:
            siblings_section = "## Sibling Modes in This Protocol\n\n"
            for sp in sibling_profiles:
                siblings_section += self._format_profile(sp) + "\n"

        return (
            "You are a protocol-adherence validator for a software "
            "development protocol.\n"
            "Your job: determine whether a submitted task spec describes "
            "work appropriate to the CURRENT mode, or whether it semantically "
            "belongs to a different mode in the protocol.\n"
            "\n"
            f"{current_section}"
            f"\n"
            f"{siblings_section}"
            f"## Task Specification\n"
            f"{context.task.spec}\n"
            f"\n"
            f"## Evaluation Criteria\n"
            f"{criteria_text}\n"
            f"\n"
            f"## Instructions\n"
            f"1. Compare the task spec against the current mode's profile "
            f"(tools, validators, transitions, name)\n"
            f"2. Consider whether the work would be more naturally performed "
            f"by a sibling mode\n"
            f"3. If the task aligns with the current mode: severity=pass\n"
            f"4. If the task describes work that belongs to a sibling mode: "
            f"severity=warn or blocker, with justification explaining which "
            f"mode it belongs in\n"
            f"5. Return JSON: {{\"severity\": \"...\", \"justification\": \"...\"}}\n"
            f"   severity must be one of: pass, warn, blocker\n"
            f"\n"
            f"Respond with ONLY the JSON object, no other text.\n"
        )

    @staticmethod
    def _format_profile(profile: Dict[str, Any]) -> str:
        """Format a mode profile for the LLM prompt."""
        lines = [
            f"### Mode: {profile['mode_name']} ({profile['mode_id']})",
        ]
        tools = profile.get("tools", [])
        if tools:
            lines.append(f"  Tools: {', '.join(sorted(tools))}")
        validators = profile.get("applied_validators", [])
        if validators:
            v_lines = [
                f"    - {v['validator_id']} ({v['validator_type']})"
                for v in validators
            ]
            lines.append("  Validators:")
            lines.extend(v_lines)
        transitions = profile.get("transitions", {})
        if transitions:
            t_lines = [
                f"    {k} → {v}" for k, v in sorted(transitions.items())
            ]
            lines.append("  Transitions:")
            lines.extend(t_lines)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM call + response parsing
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        response = self._completion_fn(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=self.completion_tokens,
        )
        return response.choices[0].message.content

    def _call_llm_structured(self, prompt: str) -> ValidatorResult:
        """Call the LLM with response_format=ValidatorResult for structured output.

        LiteLLM enforces JSON schema at the API level.  The response content
        is guaranteed to be valid JSON matching the ValidatorResult schema.
        Zero free-text parsing.
        """
        response = self._completion_fn(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=self.completion_tokens,
            response_format=ValidatorResult,
        )
        content = response.choices[0].message.content
        return ValidatorResult.model_validate_json(content)

    def _parse_response(self, response_text: str) -> ValidatorResult:
        parsed = self._try_json_parse(response_text)

        if parsed is None:
            parsed = self._try_extract_json(response_text)

        if parsed is None:
            return ValidatorResult(
                validator_id=self.validator_spec.validator_id,
                severity="warn",
                justification=f"Could not parse LLM response: {response_text[:200]}",
            )

        severity = str(parsed.get("severity", "")).lower().strip()
        justification = str(parsed.get("justification", "No justification provided"))

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

    @staticmethod
    def _try_json_parse(text: str) -> Optional[dict]:
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _try_extract_json(text: str) -> Optional[dict]:
        match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                pass
        match = re.search(r'\{[^{}]*"severity"[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass
        return None


_default_registry.register(ProtocolAdherenceValidator.registered_type(), ProtocolAdherenceValidator)
