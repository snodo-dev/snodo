#!/usr/bin/env python
"""Re-run the survey's accuracy measurement against a declared ground truth.

FILE: scripts/measure_survey.py

The survey's precision and recall were measured once, by hand, against eight
private repositories. Those repositories and their ground-truth file stay
outside this repository; this script is the instrument that makes the
measurement repeatable on demand — one command instead of an evening.

It takes a path to a ground-truth JSON file that names repositories on disk
and their expected module boundaries and languages, runs the survey over
each, and prints the precision and recall figures. By default it runs the
deterministic pass only, so it needs no network and no configured provider;
pass ``--agent`` to route boundary judgements through the configured agent.

Ground-truth file format (JSON)::

    {
      "repositories": [
        {
          "name": "example",
          "path": "/abs/path/or/relative/to/this/file",
          "boundaries": ["packages/api", "packages/web"],
          "languages": ["typescript", "svelte"],
          "test_command": "npm test"
        }
      ]
    }

``boundaries`` and ``languages`` are the scored sets; ``test_command`` is
optional and reported as a mismatch when it differs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from snodo.survey.analyzer import analyze_repository
from snodo.survey.measure import (
    CorpusMeasurement,
    load_ground_truth,
    render_measurement,
    score_survey,
)


def _resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (base / path)


def run_measurement(ground_truth_path: Path, agent_mode: str = "off") -> CorpusMeasurement:
    """Measure every repository the ground-truth file names."""
    ground_truth_path = Path(ground_truth_path)
    base = ground_truth_path.resolve().parent
    measurements = []

    for expected in load_ground_truth(ground_truth_path):
        if not expected.path:
            raise ValueError(f"ground truth entry {expected.name!r} has no path")
        root = _resolve_path(base, expected.path)

        judge = None
        if agent_mode != "off":
            from snodo.cli.commands.survey_cmd import build_survey_judge

            judge = build_survey_judge(root, agent_mode)

        survey = analyze_repository(root, judge=judge, judge_mode=agent_mode)
        measurements.append(score_survey(expected, survey))

    return CorpusMeasurement(measurements)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ground_truth", type=Path, help="path to the ground-truth JSON file")
    parser.add_argument(
        "--agent",
        choices=("off", "auto", "force"),
        default="off",
        help="consult the configured agent for boundary judgements (default: off)",
    )
    parser.add_argument(
        "--per-repo",
        action="store_true",
        help="print a line per repository, not only the totals",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a repository misses its declared answer",
    )
    args = parser.parse_args(argv)

    try:
        corpus = run_measurement(args.ground_truth, args.agent)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "boundaries": corpus.boundary_score.to_dict(),
            "languages": corpus.language_score.to_dict(),
            "mismatches": corpus.mismatches(),
            "repositories": [
                {
                    "name": m.expected.name,
                    "boundaries": m.boundary_score.to_dict(),
                    "languages": m.language_score.to_dict(),
                }
                for m in corpus.measurements
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_measurement(corpus, per_repository=args.per_repo))

    if args.check:
        return 1 if corpus.unmet_expectations() else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
