"""Machine-readable model benchmark report assembly."""

import hashlib
import statistics
from pathlib import Path
from typing import Optional


def benchmark_json_payload(model: str, prompt: str, samples: list, attempted_runs: int, prompt_path: Path) -> dict:
    """Build the stable machine payload and aggregate successful measurements."""
    successful = [sample for sample in samples if sample.get("ok")]

    def distribution(key: str) -> Optional[dict]:
        values = [sample[key] for sample in successful if sample.get(key) is not None]
        if not values:
            return None
        return {"median": statistics.median(values), "mean": statistics.mean(values)}

    from snodo.version import __version__

    return {
        "schema": "snodo.models-benchmark.v1",
        "ok": bool(successful),
        "model": model,
        "snodo_version": __version__,
        "prompt": {
            "file": str(prompt_path),
            "chars": len(prompt),
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        "attempted_runs": attempted_runs,
        "succeeded_runs": len(successful),
        "samples": samples,
        "statistics": {
            "time_to_first_token": distribution("time_to_first_token"),
            "decode_tok_per_sec": distribution("decode_tok_per_sec"),
            "overall_tok_per_sec": distribution("overall_tok_per_sec"),
        },
    }


def print_benchmark_report(model: str, prompt: str, result: dict, prompt_identity, prompt_path: Path) -> None:
    """Print one benchmark's counts, timing, and throughput."""
    def rate(value):
        return f"{value:.1f}" if value is not None else "n/a"

    ttft = result["time_to_first_token"]
    ttft_text = f"{ttft:.2f}s" if ttft is not None else "n/a"
    print(f"Benchmark result: {model}")
    print(f"  prompt       {prompt_identity(prompt)}")
    print(f"  prompt file  {prompt_path}")
    print(f"  tokens       prompt {result['prompt_tokens']} / output {result['output_tokens']}  ({result['counts_basis']})")
    if result.get("reasoning_tokens") is not None:
        print(f"  output split reasoning {result['reasoning_tokens']} / answer {result['answer_tokens']} tokens")
    print(f"  timing       first token {ttft_text}, total wall {result['wall_seconds']:.2f}s")
    answer_ttft = result.get("time_to_first_answer_token")
    if answer_ttft is not None and answer_ttft != ttft:
        print(f"               visible answer began {answer_ttft:.2f}s")
    print(f"  throughput   decode {rate(result['decode_tok_per_sec'])} output tok/s (after first token); overall {rate(result['overall_tok_per_sec'])} output tok/s (including first-token wait)")
