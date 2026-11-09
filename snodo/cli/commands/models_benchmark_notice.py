"""Inform operators about the harnesses involved in a model comparison."""

from pathlib import Path

import yaml


def _candidate_coder(root: Path, model: str, protocol: str) -> str:
    """Resolve the protocol's configured coder, matching ordinary task dispatch."""
    try:
        data = yaml.safe_load((root / protocol).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return "unknown"
    modes = data.get("modes", [])
    mode_coder = None
    if isinstance(modes, list):
        for mode in modes:
            if isinstance(mode, dict) and mode.get("coder"):
                mode_coder = str(mode["coder"])
                break
    from snodo.coders import resolve_coder_name

    return resolve_coder_name(model=model, mode_coder=mode_coder)


def print_benchmark_notice(root: Path, baseline: dict, model: str, protocol: str = ".snodo/protocol.yml") -> None:
    """Print a best-effort harness/model notice to stderr (including in JSON mode)."""
    baseline_coder = str(baseline.get("coder") or "unknown")
    baseline_model = str(baseline.get("model") or "unknown")
    candidate_coder = _candidate_coder(root, model, protocol)
    candidate_model = model
    print(f"Baseline: coder {baseline_coder}; model {baseline_model}.", file=__import__("sys").stderr)
    print(f"Candidate: coder {candidate_coder}; model {candidate_model}.", file=__import__("sys").stderr)
    if baseline_coder != "unknown" and baseline_coder != candidate_coder:
        print(f"Harness differs: baseline ran in {baseline_coder}; candidate runs in {candidate_coder}.", file=__import__("sys").stderr)


def print_validator_replay_notice(
    baseline: dict, validator_id: str, phase: str, commit: str, candidate_model: str
) -> None:
    """Print a best-effort validator replay notice to stderr (including in JSON mode)."""
    import sys

    recorded_model = "unknown"
    halt = baseline.get("halt") if isinstance(baseline.get("halt"), dict) else {}
    results = baseline.get("validator_results") or halt.get("validator_results") or []
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict) and result.get("validator_id") == validator_id:
                recorded_model = str(result.get("model") or "unknown")
                break
    print(f"Validator replay: validator {validator_id}; phase {phase}; commit {commit}.", file=sys.stderr)
    print(f"Recorded model: {recorded_model}; candidate model: {candidate_model}.", file=sys.stderr)
    print("This validator call is billed or uses your subscription.", file=sys.stderr)
