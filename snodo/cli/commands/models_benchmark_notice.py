"""Inform operators about the harnesses involved in a model comparison."""

from pathlib import Path

import yaml


def _candidate_coder(root: Path) -> str:
    """Resolve the protocol's configured coder, matching ordinary task dispatch."""
    try:
        data = yaml.safe_load((root / ".snodo" / "protocol.yml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return "unknown"
    modes = data.get("modes", [])
    if isinstance(modes, list):
        for mode in modes:
            if isinstance(mode, dict) and mode.get("coder"):
                return str(mode["coder"])
    return "litellm"


def print_benchmark_notice(root: Path, baseline: dict, model: str) -> None:
    """Print a best-effort harness/model notice to stderr (including in JSON mode)."""
    baseline_coder = str(baseline.get("coder") or "unknown")
    baseline_model = str(baseline.get("model") or "unknown")
    candidate_coder = _candidate_coder(root)
    candidate_model = model if "/" in model else f"{model}"
    print(f"Baseline: coder {baseline_coder}; model {baseline_model}.", file=__import__("sys").stderr)
    print(f"Candidate: coder {candidate_coder}; model {candidate_model}.", file=__import__("sys").stderr)
    if baseline_coder != "unknown" and baseline_coder != candidate_coder:
        print(f"Harness differs: baseline ran in {baseline_coder}; candidate runs in {candidate_coder}.", file=__import__("sys").stderr)
