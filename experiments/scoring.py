"""SWE-bench evaluation oracle wrapper.

Wraps the official swebench evaluation harness. Reads instance metadata
from the frozen selection.jsonl (never re-pulls HF).

Supports batch scoring (multiple predictions in a single harness invocation)
and caches positive-control (gold) results per instance_id.

For testing, use MockScorer which returns deterministic results.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _swebench_python() -> str:
    """Python used to invoke the swebench harness subprocess.

    Defaults to the current interpreter, but SNODO_SWEBENCH_PYTHON lets a run
    point scoring at a DIFFERENT swebench install — e.g. the FEA-Bench-patched
    swebench in its own venv (which carries FEA's per-instance env specs) —
    while dispatch/orchestration keep running in the experiments .venv. Keeps
    the FEA-patched swebench (numpy2/vllm-era deps) out of the experiments env.
    """
    return os.environ.get("SNODO_SWEBENCH_PYTHON") or sys.executable

_INSTANCE_CACHE: Dict[str, dict] = {}

# Gold-patch result cache: instance_id -> result dict.
# Positive control (gold patch) is deterministic per (instance, dataset),
# so we cache it to avoid re-running the harness on every experiment run.
_GOLD_CACHE: Dict[str, Optional[dict]] = {}


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


def _cleanup_eval_containers(run_id: str) -> None:
    """Remove any swebench eval containers spawned by this scoring run.

    swebench names its per-instance containers `sweb.eval.<iid>.<run_id>` and
    leaves them idling (`tail -f /dev/null`); a timed-out or killed harness
    leaks them, and they accumulate until Docker starves and future evals hang.
    Reaping by run_id after every scoring call keeps the scorer self-cleaning.
    Best-effort: never raise into the caller.
    """
    try:
        ids = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name={run_id}"],
            capture_output=True, text=True, timeout=30,
        ).stdout.split()
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids],
                           capture_output=True, timeout=90)
    except Exception:
        pass

# Frozen local copy of the SWE-bench records for our selection. swebench's
# HF loader re-fetches the dataset on every call and its `datasets` cache can
# go stale, intermittently raising "Some instance IDs not found in dataset!"
# mid-run. Passing a local .jsonl to --dataset_name bypasses HF entirely and
# is deterministic. Generated once from the HF dataset (see README/task docs).
_LOCAL_DATASET = Path(__file__).parent / "tasks" / "swebench_local.jsonl"


def _resolve_dataset(dataset_name: str) -> str:
    """Prefer a frozen local dataset file over the flaky HF loader.

    Per-run override via SNODO_EXP_DATASET (set by run_exp1 --dataset) so
    parallel experiments each score against their own frozen dataset; falls
    back to the repo-default local file, then the HF name.
    """
    import os
    if dataset_name.startswith("princeton-nlp/"):
        env = os.environ.get("SNODO_EXP_DATASET")
        if env and Path(env).exists():
            return env
        if _LOCAL_DATASET.exists():
            return str(_LOCAL_DATASET)
    return dataset_name


def _parse_report(report: Path, iid: str) -> dict:
    """Parse a swebench report.json and return the result dict."""
    try:
        data = json.loads(report.read_text())
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


def score_prediction(
    instance: dict,
    model_patch: str,
    model_name: str = "experiment-model",
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    timeout_s: int = 1800,
) -> dict:
    """Score a single prediction via the OFFICIAL swebench CLI harness.

    Drives ``python -m swebench.harness.run_evaluation`` (the stable, documented
    entrypoint) and parses the per-instance report.json it writes.  Uses
    ``--max_workers 1`` since there is only one prediction.

    Returns: {resolved, n_fail_to_pass_passed, regressions, error}.
    """
    return score_predictions_batch(
        instances=[(instance, model_patch, model_name)],
        dataset_name=dataset_name,
        timeout_s=timeout_s,
    ).get((instance["instance_id"], model_name), {**_FAIL, "error": "no_report"})


def score_predictions_batch(
    instances: List[Tuple[dict, str, str]],
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    timeout_s: int = 1800,
    max_workers: int = 1,
    namespace: str = "swebench",
    cache_level: str = "instance",
) -> Dict[Tuple[str, str], dict]:
    """Score multiple predictions in a SINGLE harness invocation.

    Writes all predictions into one JSONL, invokes the swebench harness ONCE
    with ``--max_workers N``, and returns a dict keyed by ``(instance_id, model_name)``
    where *model_name* is the **original** caller-provided name (not a sanitized copy).

    Uses prebuilt Docker images from *namespace* (default ``swebench``) and
    *cache_level* ``instance`` so per-task image pulls are reused across runs.

    Args:
        instances: List of ``(instance_dict, model_patch, model_name)`` tuples.
        dataset_name: HuggingFace dataset for the oracle.
        timeout_s: Per-harness-invocation timeout (wall clock).
        max_workers: Number of parallel Docker containers (``--max_workers``).
        namespace: Docker Hub namespace for prebuilt eval images.
        cache_level: Cache reuse level (``instance``, ``env``, ``none``).

    Returns:
        Dict mapping ``(instance_id, model_name)`` — with the ORIGINAL caller-provided
        model_name — to result dicts.
    """
    if not instances:
        return {}

    run_id = f"score-batch-{uuid.uuid4().hex[:10]}"

    with tempfile.TemporaryDirectory() as td:
        preds_path = Path(td) / "preds.jsonl"
        instance_ids: set[str] = set()

        # Build a map: (instance_id, original_model_name) -> safe_model_name
        # so we can look up results by the original name the caller used.
        safe_map: dict[tuple[str, str], str] = {}

        # swebench is built for ONE model_name_or_path per predictions file
        # (its summary report is named after a single model). Feeding it a
        # distinct model_name per line makes it warn "Missing predictions" and
        # skip instances. Instances within a batch are already unique by
        # instance_id, so we write every prediction under ONE constant model
        # dir and map results back to the caller's model_name by instance_id.
        batch_model = "batch"
        lines = []
        for instance, model_patch, model_name in instances:
            if not model_patch:
                continue
            iid = instance["instance_id"]
            instance_ids.add(iid)
            safe_map[(iid, model_name)] = batch_model
            lines.append(json.dumps({
                "instance_id": iid,
                "model_name_or_path": batch_model,
                "model_patch": model_patch,
            }))

        if not lines:
            return {}

        preds_path.write_text("\n".join(lines) + "\n")

        # swebench's --instance_ids is space-separated (nargs="+"). Passing a
        # single comma-joined string makes it treat the whole string as ONE
        # instance_id, which is never in the dataset -> "Some instance IDs not
        # found" -> rc=1 -> no reports -> every arm scores 0. Spread the ids as
        # separate argv tokens. (Single-instance batches happened to work only
        # because one id has no comma.)
        # --namespace <org> makes the harness PULL prebuilt eval images from that
        # Docker Hub org — correct for OFFICIAL SWE-bench (images published under
        # swebench/), but FEA-Bench images are NOT published and must be BUILT
        # locally. Omitting --namespace = local build. Override per run with
        # SNODO_SWEBENCH_NAMESPACE (set it to "none"/"" for FEA).
        ns = os.environ.get("SNODO_SWEBENCH_NAMESPACE", namespace)
        cmd = [
            _swebench_python(), "-m", "swebench.harness.run_evaluation",
            "--dataset_name", _resolve_dataset(dataset_name),
            "--predictions_path", str(preds_path),
            "--instance_ids", *sorted(instance_ids),
            "--max_workers", str(max_workers),
            "--run_id", run_id,
            "--cache_level", cache_level,
        ]
        if ns and ns.lower() not in ("none", "local", ""):
            cmd += ["--namespace", ns]

        try:
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout_s,
                    cwd=td,
                )
            except subprocess.TimeoutExpired:
                return {
                    (inst["instance_id"], mn): {**_FAIL, "error": f"harness timeout >{timeout_s}s"}
                    for inst, _, mn in instances
                }
            except FileNotFoundError as exc:
                return {
                    (inst["instance_id"], mn): {**_FAIL, "error": f"swebench not runnable: {exc}"}
                    for inst, _, mn in instances
                }

            # PRIMARY source: the run summary the harness always writes to cwd
            # as `<model>.<run_id>.json` (resolved_ids / error_ids / empty_patch_ids).
            # It is version-stable and survives the harness's result caching, which
            # can skip re-writing the per-instance report.json into this tempdir
            # (seen on the FEA-patched a0536ee harness -> "no report.json").
            summary: dict = {}
            for _sp in Path(td).glob(f"*.{run_id}.json"):
                try:
                    summary = json.loads(_sp.read_text())
                    break
                except Exception:
                    pass
            resolved_ids = set(summary.get("resolved_ids", []))
            error_ids = set(summary.get("error_ids", []))
            empty_ids = set(summary.get("empty_patch_ids", []))

            # SECONDARY: per-instance report tree, for pass/fail counts when present.
            safe_results: Dict[Tuple[str, str], dict] = {}
            log_root = Path(td) / "logs" / "run_evaluation" / run_id
            if log_root.exists():
                for report in log_root.rglob("report.json"):
                    parts = report.relative_to(log_root).parts
                    if len(parts) >= 2:
                        safe_results[(parts[1], parts[0])] = _parse_report(report, parts[1])

            results: Dict[Tuple[str, str], dict] = {}
            for instance, model_patch, model_name in instances:
                iid = instance["instance_id"]
                safe = safe_map.get((iid, model_name), model_name.replace("/", "__"))
                key = (iid, model_name)
                safe_key = (iid, safe)
                if safe_key in safe_results:            # full detail from report.json
                    results[key] = safe_results[safe_key]
                elif iid in resolved_ids:               # summary says resolved
                    results[key] = {"resolved": True, "n_fail_to_pass_passed": 0, "regressions": 0}
                elif iid in empty_ids or not model_patch:
                    results[key] = {**_FAIL, "error": "empty_patch"}
                elif iid in error_ids:
                    results[key] = {**_FAIL, "error": "harness error (per summary)"}
                elif summary:                           # ran, not resolved -> genuine fail
                    results[key] = {**_FAIL}
                else:                                   # no summary at all -> real infra failure
                    tail = (proc.stderr or proc.stdout or "")[-400:]
                    results[key] = {**_FAIL, "error": f"no report.json (rc={proc.returncode}). {tail}"}

            return results
        finally:
            # Always reap this run's eval containers — even on timeout/exception.
            # swebench leaves `sweb.eval.<iid>.<run_id>` containers idling on
            # `tail -f /dev/null`; a killed/timed-out harness leaks them, and they
            # pile up (seen: containers Up 28-39h) until Docker starves and new
            # evals hang. Removing by run_id keeps scoring self-cleaning.
            _cleanup_eval_containers(run_id)


def _gold_cache_key(instance: dict, dataset_name: str) -> str:
    return f"{instance['instance_id']}@{dataset_name}"


def _cached_gold_result(instance: dict, dataset_name: str = "princeton-nlp/SWE-bench_Verified") -> Optional[dict]:
    """Return a cached gold result, or None if not yet scored."""
    return _GOLD_CACHE.get(_gold_cache_key(instance, dataset_name))


def _set_cached_gold_result(instance: dict, result: dict, dataset_name: str = "princeton-nlp/SWE-bench_Verified") -> None:
    """Store a gold result in the cache."""
    _GOLD_CACHE[_gold_cache_key(instance, dataset_name)] = result


def clear_gold_cache() -> None:
    """Clear the gold-patch result cache (e.g. between runs)."""
    _GOLD_CACHE.clear()


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

    def score_batch(
        self,
        instances: List[Tuple[dict, str, str]],
        max_workers: int = 1,
    ) -> Dict[Tuple[str, str], dict]:
        """Score multiple predictions (mock)."""
        return {
            (inst["instance_id"], name): self.score(inst, patch, name)
            for inst, patch, name in instances
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
    """Real scorer — delegates to the swebench harness via score_prediction.

    Supports batch scoring (``score_batch``) and caches gold-patch results.
    """

    def __init__(self, max_workers: int = 1, namespace: str = "swebench", cache_level: str = "instance"):
        self._max_workers = max_workers
        self._namespace = namespace
        self._cache_level = cache_level

    def score(
        self,
        instance: dict,
        model_patch: str,
        model_name: str = "experiment-model",
    ) -> dict:
        return score_prediction(instance, model_patch, model_name)

    def score_batch(
        self,
        instances: List[Tuple[dict, str, str]],
        max_workers: Optional[int] = None,
    ) -> Dict[Tuple[str, str], dict]:
        """Score multiple predictions in a single harness invocation.

        Args:
            instances: List of ``(instance_dict, model_patch, model_name)``.
            max_workers: Override the scorer's default max_workers.

        Returns:
            Dict mapping ``(instance_id, model_name)`` to result dicts.
        """
        return score_predictions_batch(
            instances,
            max_workers=max_workers if max_workers is not None else self._max_workers,
            namespace=self._namespace,
            cache_level=self._cache_level,
        )

    def score_prediction_record(self, instance: dict, prediction: dict) -> dict:
        return self.score(
            instance,
            prediction.get("model_patch", ""),
            prediction.get("model_name_or_path", "experiment-model"),
        )

    def score_gold_with_cache(
        self,
        instance: dict,
        dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    ) -> dict:
        """Score the gold patch, using/updating the cache.

        The gold (reference) patch is deterministic per instance — caching it
        across runs saves harness startup time.
        """
        cached = _cached_gold_result(instance, dataset_name)
        if cached is not None:
            return cached

        gold_patch = instance.get("gold_patch") or instance.get("patch", "")
        if not gold_patch:
            result = {**_FAIL, "error": "no_gold_patch"}
            _set_cached_gold_result(instance, result, dataset_name)
            return result

        result = score_prediction(instance, gold_patch, "gold-baseline", dataset_name=dataset_name)
        _set_cached_gold_result(instance, result, dataset_name)
        return result

    def __call__(self, instance: dict, model_patch: str, **kwargs) -> dict:
        return self.score(instance, model_patch, **kwargs)


def make_scorer(mock: bool = False, max_workers: int = 1, namespace: str = "swebench", cache_level: str = "instance", **kwargs) -> MockScorer | RealScorer:
    """Factory: return a MockScorer or a RealScorer (both expose .score).

    Args:
        mock: If True, return MockScorer (no Docker needed).
        max_workers: Default parallelism for RealScorer.score_batch.
        namespace: Docker Hub namespace for prebuilt eval images.
        cache_level: Cache reuse level (``instance``, ``env``, ``none``).
    """
    if mock:
        return MockScorer(**kwargs)
    return RealScorer(max_workers=max_workers, namespace=namespace, cache_level=cache_level)
