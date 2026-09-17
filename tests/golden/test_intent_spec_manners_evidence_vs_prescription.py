"""spec-manners' code-prescriptive criterion must separate evidence from
prescription (Fixes #322).

The criterion used to read only "WARN if the spec is code-prescriptive
(transcribed implementation) rather than intent + constraints." That gives a
judge nothing to be consistent about: a spec that cites a file and line as
the location of an OBSERVED symptom mentions a path just as surely as a spec
that transcribes an implementation, so both looked alike to a judge scanning
for "mentions code". On a real project this produced opposite verdicts for
tasks of the same shape.

The fix states the distinction in the criterion itself. This module checks
two things:

1. The shipped text actually carries a citable distinction (not just a
   restatement of the WARN condition).
2. Given that distinction, a judge that reads it and applies it mechanically
   reaches different, correct verdicts for the two representative specs from
   the ticket — one citing a path as the location of an observed symptom,
   one transcribing an implementation. This suite runs hermetically (no
   network / live model calls anywhere in it), so the second check exercises
   the real LLMValidator plumbing with a scripted completion function
   standing in for a compliant judge, over the real spec-manners validator
   loaded from the shipped template.
"""

from pathlib import Path

import snodo.protocols
import yaml
from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.validators.llm_validator import LLMValidator

TEMPLATES_DIR = Path(snodo.protocols.__file__).parent / "templates"


def _load_intent() -> Protocol:
    data = yaml.safe_load((TEMPLATES_DIR / "intent.yml").read_text())
    return Protocol(**data)


def _code_prescriptive_criterion() -> str:
    spec_manners = _load_intent().get_validator("spec-manners")
    assert spec_manners is not None
    matches = [c for c in spec_manners.criteria if "code-prescriptive" in c]
    assert len(matches) == 1, spec_manners.criteria
    return matches[0]


class TestCriterionStatesTheDistinction:
    def test_names_evidence_as_grounding_present_state(self):
        criterion = _code_prescriptive_criterion()
        assert "evidence" in criterion.lower()
        assert "observed" in criterion.lower()

    def test_names_prescription_as_dictating_the_change(self):
        criterion = _code_prescriptive_criterion()
        assert "shape of the change" in criterion.lower() or "dictat" in criterion.lower()

    def test_gives_a_worked_example_of_each_side(self):
        criterion = _code_prescriptive_criterion()
        # Evidence side: naming a location.
        assert "file and line" in criterion.lower()
        # Prescription side: a sequence of edits or code to reproduce.
        assert "sequence of edits" in criterion.lower() or "reproduce" in criterion.lower()

    def test_tells_the_judge_what_to_cite(self):
        """The old wording gave no hook for a justification; the fix asks the
        judge to cite the prescriptive text specifically, not just the path."""
        criterion = _code_prescriptive_criterion()
        assert "cite" in criterion.lower()

    def test_not_the_old_undifferentiated_wording(self):
        criterion = _code_prescriptive_criterion()
        assert criterion != (
            "WARN if the spec is code-prescriptive (transcribed implementation) "
            "rather than intent + constraints."
        )


# ---------------------------------------------------------------------------
# End-to-end: a judge applying the stated distinction reaches different,
# correct verdicts for the two representative spec shapes.
# ---------------------------------------------------------------------------

SPEC_CITING_OBSERVED_LOCATION = """\
Fix the crash when a session token expires mid-request.
Acceptance: a request arriving after token expiry no longer crashes; a
regression test covers it.
Scope: session handling in the auth module.
The symptom was observed at src/auth/session.py:142, where the
expired-token branch dereferences session.user before checking it is not
None.
"""

SPEC_TRANSCRIBING_IMPLEMENTATION = """\
Fix the crash when a session token expires mid-request.
Acceptance: a request arriving after token expiry no longer crashes; a
regression test covers it.
Scope: session handling in the auth module.
Implement it exactly as follows in src/auth/session.py:
```
if session.user is None:
    raise ExpiredSessionError()
user = session.user
```
"""


def _compliant_judge_completion_fn(spec_text: str):
    """A completion function standing in for a judge that reads the fixed
    criterion and applies its evidence-vs-prescription distinction: a fenced
    block of code the coder is meant to reproduce is prescription; a bare
    file:line citation of where a symptom was observed is evidence."""
    import json
    from unittest.mock import MagicMock

    if "```" in spec_text:
        payload = {
            "severity": "warn",
            "justification": (
                "The spec transcribes the implementation (a code block the "
                "coder is meant to reproduce) rather than stating intent and "
                "constraints."
            ),
        }
    else:
        payload = {
            "severity": "pass",
            "justification": (
                "The spec cites a file and line only as the location of an "
                "observed symptom — that is evidence, not prescription."
            ),
        }

    def completion_fn(**kwargs):
        msg = MagicMock()
        msg.content = json.dumps(payload)
        resp = MagicMock()
        resp.choices = [MagicMock(message=msg)]
        return resp

    return completion_fn


def _evaluate(spec_text: str):
    spec_manners = _load_intent().get_validator("spec-manners")
    validator = LLMValidator(spec_manners, _compliant_judge_completion_fn(spec_text))
    return validator.evaluate(Task(id="t1", spec=spec_text))


class TestJudgeAppliesTheDistinctionConsistently:
    def test_spec_citing_observed_location_is_not_flagged_prescriptive(self):
        result = _evaluate(SPEC_CITING_OBSERVED_LOCATION)
        assert result.severity == "pass", result.justification

    def test_spec_transcribing_implementation_is_still_flagged(self):
        result = _evaluate(SPEC_TRANSCRIBING_IMPLEMENTATION)
        assert result.severity == "warn", result.justification
        assert "transcrib" in result.justification.lower()
