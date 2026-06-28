"""Deterministic, config-driven task selection from SWE-bench datasets.

Reads ALL knobs from experiments/config.py (selection.*). Downloads the
named dataset from HuggingFace, filters + stratifies, and writes the frozen
task set to experiments/tasks/.

The committed output IS the pre-registration of the task set.
"""

import csv
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from experiments.config import load_config, write_snapshot

_HERE = Path(__file__).resolve().parent
_TASKS_DIR = _HERE / "tasks"

DIFFICULTY_MAP = {
    "<15 min fix": "easy",
    "15 min - 1 hour": "medium",
    "1-4 hours": "hard",
    ">4 hours": "hard",
}

DIFFICULTY_SOURCE_VERIFIED = "swe_bench_verified_label"
DIFFICULTY_SOURCE_LOC_PROXY = "loc_proxy"


def _parse_ft_patch(patch_str: str) -> dict:
    """Parse a FAIL_TO_PASS / PASS_TO_PASS string into a list of test dicts.

    SWE-bench encodes these as JSON inside the dataset column. If already
    parsed, pass through.
    """
    if isinstance(patch_str, list):
        return {"raw": patch_str, "count": len(patch_str)}
    if not isinstance(patch_str, str):
        return {"raw": [], "count": 0}
    try:
        parsed = json.loads(patch_str)
        if isinstance(parsed, list):
            return {"raw": parsed, "count": len(parsed)}
        return {"raw": [], "count": 0}
    except (json.JSONDecodeError, TypeError):
        return {"raw": [patch_str], "count": 1}


def _patch_stats(patch: str) -> dict:
    """Return n_files, added_loc from a unified diff patch string."""
    if not patch:
        return {"n_files": 0, "added_loc": 0}
    files = set()
    added = 0
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            files.add(line.split()[-1])
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
    return {"n_files": len(files), "added_loc": added}


def _contamination_flag(created_at: str, model_cutoff: Optional[str]) -> str:
    """Compare task created_at against model cutoff date.

    Returns "contaminated" if created_at after cutoff, "unknown" if no cutoff,
    "clean" otherwise. Cutoff format: YYYY-MM-DD.
    """
    if not model_cutoff:
        return "unknown"
    if not created_at:
        return "unknown"
    try:
        task_date = created_at[:10]
        return "contaminated" if task_date > model_cutoff else "clean"
    except (IndexError, ValueError):
        return "unknown"


def _load_dataset(path: Path) -> List[dict]:
    """Load a SWE-bench dataset from a JSONL file (offline) or HF dataset name."""
    if path.suffix == ".jsonl":
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    # Otherwise try HuggingFace datasets
    from datasets import load_dataset
    ds_name = str(path)
    try:
        ds = load_dataset(ds_name, split="test")
        return [dict(row) for row in ds]
    except Exception as e:
        print(f"Error loading dataset {ds_name}: {e}", file=sys.stderr)
        sys.exit(1)


def _filter_and_enrich(rows: List[dict], config: dict) -> List[dict]:
    """Apply frozen filters and enrich with computed fields."""
    min_n_files = 0
    max_n_files = 3
    out = []
    for row in rows:
        patch = row.get("patch", "") or ""
        test_patch = row.get("test_patch", "") or ""
        ftp_raw = row.get("FAIL_TO_PASS", [])
        ftp = _parse_ft_patch(ftp_raw)

        # Frozen filters
        if ftp["count"] == 0:
            continue
        if not patch:
            continue

        stats = _patch_stats(patch)
        n_files = stats["n_files"]
        if n_files < min_n_files or n_files > max_n_files:
            continue

        # Difficulty
        raw_diff = row.get("difficulty", "")
        difficulty = DIFFICULTY_MAP.get(raw_diff, "unknown")
        diff_source = DIFFICULTY_SOURCE_VERIFIED if raw_diff in DIFFICULTY_MAP else DIFFICULTY_SOURCE_LOC_PROXY

        # Model cutoff for contamination
        model_cutoff = config.get("models", {}).get("cutoff")
        contamination = _contamination_flag(row.get("created_at", ""), model_cutoff)

        enriched = {
            "instance_id": row.get("instance_id", ""),
            "repo": row.get("repo", ""),
            "base_commit": row.get("base_commit", ""),
            "environment_setup_commit": row.get("environment_setup_commit", ""),
            "version": str(row.get("version", "")),
            "created_at": row.get("created_at", ""),
            "difficulty": difficulty,
            "difficulty_source": diff_source,
            "n_files": n_files,
            "added_loc": stats["added_loc"],
            "fail_to_pass": ftp["raw"] if isinstance(ftp["raw"], list) else [],
            "pass_to_pass": _parse_ft_patch(row.get("PASS_TO_PASS", [])).get("raw", []),
            "problem_statement": row.get("problem_statement", ""),
            "gold_patch": patch,
            "test_patch": test_patch,
            "contamination_flag": contamination,
        }
        out.append(enriched)
    return out


def _stratified_pick(
    candidates: List[dict],
    strata: List[str],
    n_total: int,
    min_repos: int,
    seed: int,
) -> List[dict]:
    """Stratified sampling across difficulty strata.

    Allocates n_total proportionally to stratum pool sizes, with at least 1
    per requested stratum. Falls back to filling remaining slots from the
    largest stratum.

    Returns exactly n_total candidates spread across >= min_repos distinct repos.
    """
    rng = random.Random(seed)

    # Group by difficulty stratum
    by_stratum: Dict[str, List[dict]] = defaultdict(list)
    for c in candidates:
        d = c.get("difficulty", "unknown")
        if d in strata:
            by_stratum[d].append(c)

    # Check each requested stratum has at least 1
    for s in strata:
        if s not in by_stratum or not by_stratum[s]:
            avail = ", ".join(sorted(by_stratum.keys()))
            raise ValueError(
                f"Stratum '{s}' has 0 candidates (available: {avail}). "
                "Adjust config strata or check pool."
            )

    # Allocate proportionally, minimum 1 per stratum
    total_pool = sum(len(v) for v in by_stratum.values())
    allocation: Dict[str, int] = {}
    allocated = 0
    for s in strata:
        pool = len(by_stratum[s])
        share = max(1, round(n_total * pool / total_pool))
        allocation[s] = share
        allocated += share

    # Adjust if over/under allocated
    diff = n_total - allocated
    if diff > 0:
        # Give extras to the largest stratum
        largest = max(strata, key=lambda s: len(by_stratum[s]))
        allocation[largest] += diff
    elif diff < 0:
        # Take from largest stratum (but keep at least 1)
        largest = max(strata, key=lambda s: len(by_stratum[s]))
        allocation[largest] = max(1, allocation[largest] + diff)

    selected: List[dict] = []
    used_repos: set = set()
    # Phase 1: pick one per stratum, ensuring repo diversity
    for s in strata:
        pool = sorted(by_stratum[s], key=lambda x: x["instance_id"])
        rng.shuffle(pool)
        allocation[s] -= 1
        selected.append(pool[0])
        used_repos.add(pool[0]["repo"])
        by_stratum[s] = pool[1:]

    # Phase 2: fill remaining, prioritizing repo diversity
    for s in strata:
        pool = list(by_stratum[s])
        rng.shuffle(pool)
        need = allocation[s]
        # First pass: picks from repos not yet used
        first_pass = [c for c in pool if c["repo"] not in used_repos]
        rng.shuffle(first_pass)
        for c in first_pass[:need]:
            selected.append(c)
            used_repos.add(c["repo"])
            pool.remove(c)
            need -= 1
        # Second pass: any remaining
        for c in pool[:need]:
            selected.append(c)
            used_repos.add(c["repo"])
            need -= 1

    # Final repo diversity check
    distinct_repos = len({c["repo"] for c in selected})
    if distinct_repos < min_repos:
        raise ValueError(
            f"Selected {len(selected)} tasks across only {distinct_repos} repos "
            f"(minimum {min_repos} required). Increase n or relax min_repos."
        )

    # Sort by instance_id for deterministic ordering
    selected.sort(key=lambda x: x["instance_id"])
    return selected


def select_tasks(
    dataset_path: str = "princeton-nlp/SWE-bench_Verified",
    cli_overrides: Optional[List[str]] = None,
    output_dir: Optional[Path] = None,
) -> List[dict]:
    """Main entry point: load, filter, pick, and write task selection.

    Args:
        dataset_path: HF dataset name or path to local JSONL fixture.
        cli_overrides: ``--set`` style CLI overrides.
        output_dir: Output directory (defaults to experiments/tasks/).

    Returns:
        The selected task list.
    """
    config = load_config(cli_overrides=cli_overrides)
    sel = config["selection"]

    if output_dir is None:
        output_dir = _TASKS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading dataset: {dataset_path}")
    all_rows = _load_dataset(Path(dataset_path))

    print(f"  Raw rows: {len(all_rows)}")
    candidates = _filter_and_enrich(all_rows, config)
    print(f"  After filters: {len(candidates)}")

    # Pool stats
    pool_by_stratum: Dict[str, int] = Counter()
    for c in candidates:
        pool_by_stratum[c["difficulty"]] += 1

    print(f"  Pool: {dict(pool_by_stratum)}")

    selected = _stratified_pick(
        candidates,
        strata=sel["strata"],
        n_total=sel["n"],
        min_repos=sel["min_repos"],
        seed=sel["seed"],
    )
    print(f"  Selected: {len(selected)}")

    # Write pool stats
    pool_path = output_dir / "pool_stats.txt"
    with open(pool_path, "w") as f:
        f.write("Pool stats before selection:\n")
        for s in sorted(pool_by_stratum):
            f.write(f"  {s}: {pool_by_stratum[s]}\n")
        f.write(f"\nSelected: {len(selected)}\n")
        for s in sorted(set(c["difficulty"] for c in selected)):
            cnt = sum(1 for c in selected if c["difficulty"] == s)
            f.write(f"  {s}: {cnt}\n")
        distinct_repos = len({c["repo"] for c in selected})
        f.write(f"\nDistinct repos: {distinct_repos}\n")
        for r in sorted({c["repo"] for c in selected}):
            f.write(f"  {r}\n")

    print(f"  Pool stats: {pool_path}")

    # Write selection.jsonl
    jsonl_path = output_dir / "selection.jsonl"
    with open(jsonl_path, "w") as f:
        for row in selected:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"  Selection JSONL: {jsonl_path}")

    # Write selection.csv
    csv_path = output_dir / "selection.csv"
    csv_keys = [
        "instance_id", "repo", "difficulty", "difficulty_source",
        "n_files", "added_loc", "created_at", "contamination_flag",
        "n_fail_to_pass",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_keys)
        w.writeheader()
        for row in selected:
            w.writerow({
                "instance_id": row.get("instance_id", ""),
                "repo": row.get("repo", ""),
                "difficulty": row.get("difficulty", ""),
                "difficulty_source": row.get("difficulty_source", ""),
                "n_files": row.get("n_files", 0),
                "added_loc": row.get("added_loc", 0),
                "created_at": row.get("created_at", ""),
                "contamination_flag": row.get("contamination_flag", ""),
                "n_fail_to_pass": len(row.get("fail_to_pass", [])),
            })
    print(f"  Selection CSV: {csv_path}")

    # Write the committed config.yml for provenance
    snap_path = output_dir / "experiment_config.yml"
    write_snapshot(output_dir, config)
    print(f"  Config snapshot: {snap_path}")

    print(f"\nDone — {len(selected)} tasks selected across "
          f"{len({c['repo'] for c in selected})} repos")
    return selected


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Select experiment tasks")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified")
    parser.add_argument("--set", action="append", dest="overrides", default=[])
    args = parser.parse_args()
    select_tasks(dataset_path=args.dataset, cli_overrides=args.overrides)
