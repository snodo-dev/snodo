"""Generate a stratified-by-repo selection of SWE-bench Verified tasks.

Deterministic (seeded) so the selection is reproducible and re-usable across
EXP1/EXP2. Samples across repos with a per-repo cap so no single project
(django dominates Verified) overwhelms the set. Writes BOTH:

  - selection.jsonl        (the frozen task list the runner reads)
  - swebench_local.jsonl   (full records passed to the harness --dataset_name)

Both contain the full SWE-bench records, so the harness never touches HF.

Usage:
    python -m experiments.tasks.generate_selection            # 100 tasks
    python -m experiments.tasks.generate_selection 100 7      # N=100, seed=7
"""

from __future__ import annotations

import collections
import json
import math
import random
import sys
from pathlib import Path

DATASET = "princeton-nlp/SWE-bench_Verified"
HERE = Path(__file__).parent


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 13
    rng = random.Random(seed)

    from datasets import load_dataset
    ds = [dict(r) for r in load_dataset(DATASET, split="test")]
    by_repo = collections.defaultdict(list)
    for r in ds:
        by_repo[r["repo"]].append(r)
    for repo in by_repo:
        rng.shuffle(by_repo[repo])

    repos = sorted(by_repo)
    # Per-repo cap so no repo exceeds ~15% of the set.
    cap = max(1, math.ceil(n * 0.15))
    picked: list[dict] = []
    # Round-robin across repos (each capped) until we hit n.
    idx = {repo: 0 for repo in repos}
    taken = collections.Counter()
    while len(picked) < n:
        progressed = False
        for repo in repos:
            if len(picked) >= n:
                break
            if taken[repo] >= cap:
                continue
            if idx[repo] < len(by_repo[repo]):
                picked.append(by_repo[repo][idx[repo]])
                idx[repo] += 1
                taken[repo] += 1
                progressed = True
        if not progressed:
            break  # exhausted available instances under the cap

    rng.shuffle(picked)

    sel_path = HERE / "selection.jsonl"
    loc_path = HERE / "swebench_local.jsonl"
    with open(sel_path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in picked) + "\n")
    with open(loc_path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in picked) + "\n")

    dist = collections.Counter(r["repo"] for r in picked)
    print(f"wrote {len(picked)} tasks (seed={seed}, cap={cap}/repo) to:")
    print(f"  {sel_path}")
    print(f"  {loc_path}")
    print("repo distribution:")
    for repo, c in dist.most_common():
        print(f"  {c:3d}  {repo}")


if __name__ == "__main__":
    main()
