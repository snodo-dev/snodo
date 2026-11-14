"""Pure comparison and rendering for recorded and replayed validator results."""

from typing import Any

from snodo.cli.commands.plan_compare import OUTCOME_RANK
from snodo.cli.json_output import schema_name

_METRICS = ("elapsed_seconds", "input_tokens", "output_tokens", "cost")


def compare_validator_verdicts(recorded: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare validator verdicts; only severity contributes to direction."""
    baseline_severity = str(recorded.get("severity", "unknown")).lower()
    candidate_severity = str(candidate.get("severity", "unknown")).lower()
    delta = OUTCOME_RANK.get(candidate_severity, 0) - OUTCOME_RANK.get(baseline_severity, 0)
    metrics = {}
    for key in _METRICS:
        baseline, replay = recorded.get(key), candidate.get(key)
        metrics[key] = {
            "baseline": baseline,
            "candidate": replay,
            "difference": replay - baseline if baseline is not None and replay is not None else None,
        }
    return {
        "baseline_severity": baseline_severity,
        "candidate_severity": candidate_severity,
        "direction": "better" if delta > 0 else "worse" if delta < 0 else "same",
        "justification_differs": recorded.get("justification") != candidate.get("justification"),
        "justifications": {
            "baseline": recorded.get("justification"),
            "candidate": candidate.get("justification"),
        },
        "metrics": metrics,
    }


def json_record(comparison: dict[str, Any]) -> dict[str, Any]:
    """Return the versioned machine-interface payload."""
    return {"schema": schema_name("models-validator-compare"), **comparison}


def render_human(comparison: dict[str, Any]) -> str:
    """Render a compact side-by-side validator comparison."""
    lines = [
        f"Validator verdict: {comparison['baseline_severity']} -> {comparison['candidate_severity']} ({comparison['direction']})",
        f"  justification differs: {'yes' if comparison['justification_differs'] else 'no'}",
    ]
    for key, label, suffix in (
        ("elapsed_seconds", "elapsed time", "s"), ("input_tokens", "input tokens", ""),
        ("output_tokens", "output tokens", ""), ("cost", "cost", ""),
    ):
        pair = comparison["metrics"][key]
        def show(value):
            return "n/a" if value is None else f"{value:g}{suffix}"
        difference = pair["difference"]
        diff_text = "n/a" if difference is None else f"{difference:+g}{suffix}"
        lines.append(f"  {label}: {show(pair['baseline'])} -> {show(pair['candidate'])} (difference {diff_text}; not scored)")
    return "\n".join(lines)
