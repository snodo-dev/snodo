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
