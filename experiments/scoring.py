"""SWE-bench evaluation oracle wrapper.

Wraps the official swebench evaluation harness. Reads instance metadata
from the frozen selection.jsonl (never re-pulls HF).

For testing, use MockScorer which returns deterministic results.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Optional

_INSTANCE_CACHE: Dict[str, dict] = {}


def _load_instances(selection_path: Path) -> Dict[str, dict]:
    """Load instance metadata from selection.jsonl into cache."""
    if not _INSTANCE_CACHE:
        with open(selection_path) as f:
            for line in f:
                row = json.loads(line.strip())
                _INSTANCE_CACHE[row["instance_id"]] = row
    return _INSTANCE_CACHE


def get_instance(instance_id: str, selection_path: Path) -> Optional[dict]:
    """Get instance metadata by instance_id from the frozen selection."""
    instances = _load_instances(selection_path)
    return instances.get(instance_id)


_FAIL = {"resolved": False, "n_fail_to_pass_passed": 0, "regressions": 0}


def score_prediction(
    instance: dict,
    model_patch: str,
    model_name: str = "experiment-model",
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    timeout_s: int = 2400,
) -> dict:
    """Score a single prediction via the OFFICIAL swebench CLI harness.

    Drives `python -m swebench.harness.run_evaluation` (the stable, documented
    entrypoint) and parses the per-instance report.json it writes. We use the
    CLI rather than the in-process API because the in-process signatures drift
    between swebench versions; the CLI + report.json schema are stable.

    Note: this loads the instance from `dataset_name` (the oracle's own data) so
    test_patch / FAIL_TO_PASS / PASS_TO_PASS are authoritative. That is a scoring
    fetch — it does NOT affect the frozen selection.jsonl.

    Returns: {resolved, n_fail_to_pass_passed, regressions, error}.
    """
    if not model_patch:
        return {**_FAIL, "error": "empty_patch"}

    iid = instance["instance_id"]
    run_id = f"score-{uuid.uuid4().hex[:10]}"
    safe_model = model_name.replace("/", "__")

    with tempfile.TemporaryDirectory() as td:
        preds_path = Path(td) / "preds.jsonl"
        preds_path.write_text(json.dumps({
            "instance_id": iid,
            "model_name_or_path": model_name,
            "model_patch": model_patch,
        }) + "\n")

        cmd = [
            sys.executable, "-m", "swebench.harness.run_evaluation",
            "--dataset_name", dataset_name,
            "--predictions_path", str(preds_path),
            "--instance_ids", iid,
            "--max_workers", "1",
            "--run_id", run_id,
            "--cache_level", "env",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s,
                cwd=td,  # run inside temp dir so swebench's logs/ + summary JSON
                         # do NOT litter the repo root
            )
        except subprocess.TimeoutExpired:
            return {**_FAIL, "error": f"harness timeout >{timeout_s}s"}
        except FileNotFoundError as exc:
            return {**_FAIL, "error": f"swebench not runnable: {exc}"}

        # Report under <td>/logs/run_evaluation/...; read it BEFORE the temp dir
        # is removed on exiting the `with` block.
        report = (
            Path(td) / "logs" / "run_evaluation" / run_id
            / safe_model / iid / "report.json"
        )
        if not report.exists():
            run_dir = Path(td) / "logs" / "run_evaluation" / run_id
            matches = list(run_dir.rglob("report.json")) if run_dir.exists() else []
            report = matches[0] if matches else None

        if not report or not report.exists():
            tail = (proc.stderr or proc.stdout or "")[-800:]
            return {**_FAIL, "error": f"no report.json (rc={proc.returncode}). {tail}"}

        try:
            report_text = report.read_text()
        except Exception as exc:
            return {**_FAIL, "error": f"unreadable report.json: {exc}"}

    try:
        data = json.loads(report_text)
    except Exception as exc:
        return {**_FAIL, "error": f"invalid report.json: {exc}"}

    rec = data.get(iid, {})
    tests = rec.get("tests_status", {})
    f2p = tests.get("FAIL_TO_PASS", {})
    p2p = tests.get("PASS_TO_PASS", {})
    return {
        "resolved": bool(rec.get("resolved", False)),
        "n_fail_to_pass_passed": len(f2p.get("success", [])),
        "regressions": len(p2p.get("failure", [])),
        "error": None,
    }


class MockScorer:
    """Mock scorer for testing — no Docker or swebench required.

    Gold patches always resolve. All other patches fail by default.
    """

    def __init__(self, gold_resolves: bool = True):
        self._gold_resolves = gold_resolves

    def _gold_patch(self, instance: dict) -> str:
        """Return the gold/reference patch from instance, regardless of key name."""
        return instance.get("gold_patch") or instance.get("patch", "")

    def score(
        self,
        instance: dict,
        model_patch: str,
        model_name: str = "mock-model",
    ) -> dict:
        """Score a single prediction (mock implementation)."""
        if not model_patch:
            return {
                "resolved": False,
                "n_fail_to_pass_passed": 0,
                "regressions": 0,
                "error": "empty_patch",
            }
        is_gold = model_patch == self._gold_patch(instance)
        if is_gold and self._gold_resolves:
            return {
                "resolved": True,
                "n_fail_to_pass_passed": 5,
                "regressions": 0,
                "error": None,
            }
        return {
            "resolved": False,
            "n_fail_to_pass_passed": 0,
            "regressions": 3,
            "error": None,
        }

    def score_prediction_record(self, instance: dict, prediction: dict) -> dict:
        """Score from a prediction record dict."""
        return self.score(
            instance,
            prediction.get("model_patch", ""),
            prediction.get("model_name_or_path", "mock-model"),
        )

    def __call__(self, instance: dict, model_patch: str, **kwargs) -> dict:
        return self.score(instance, model_patch, **kwargs)


class RealScorer:
    """Real scorer — same interface as MockScorer, delegates to the swebench
    harness via score_prediction. Exceptions from the harness are caught inside
    score_prediction and surfaced as the result's `error` field (resolved=False),
    so a misconfigured harness is reported, not crashed on."""

    def score(
        self,
        instance: dict,
        model_patch: str,
        model_name: str = "experiment-model",
    ) -> dict:
        return score_prediction(instance, model_patch, model_name)

    def score_prediction_record(self, instance: dict, prediction: dict) -> dict:
        return self.score(
            instance,
            prediction.get("model_patch", ""),
            prediction.get("model_name_or_path", "experiment-model"),
        )

    def __call__(self, instance: dict, model_patch: str, **kwargs) -> dict:
        return self.score(instance, model_patch, **kwargs)


def make_scorer(mock: bool = False, **kwargs) -> MockScorer | RealScorer:
    """Factory: return a MockScorer or a RealScorer (both expose .score)."""
    if mock:
        return MockScorer(**kwargs)
    return RealScorer()
