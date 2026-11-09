"""Resolve recorded inputs needed to replay a baseline validator."""

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from snodo.cli.commands.models_benchmark import _task_spec


@dataclass(frozen=True)
class ValidatorReplayInput:
    """Inputs recorded for a single validator verdict."""

    spec: str
    phase: str
    base_commit: str
    head_commit: str
    result: dict


def resolve_validator_replay_input(
    project_root: Path | str, plan: str, task: str, validator_id: str
) -> ValidatorReplayInput:
    """Resolve baseline data and protocol phase; does not execute validators."""
    root = Path(project_root)
    baseline_path = root / ".snodo" / "baselines" / plan / task / "baseline.json"
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read baseline for '{plan}/{task}' at {baseline_path}: {exc}") from exc
    results = baseline.get("validator_results", [])
    if not isinstance(results, list):
        results = []
    result = next((item for item in results if isinstance(item, dict) and item.get("validator_id") == validator_id), None)
    if result is None:
        ids = sorted({str(item.get("validator_id")) for item in results if isinstance(item, dict) and item.get("validator_id")})
        raise ValueError(f"Validator '{validator_id}' is not recorded in baseline; recorded validator ids: {', '.join(ids) or '(none)'}")
    change_size = baseline.get("change_size") if isinstance(baseline.get("change_size"), dict) else {}
    base, head = change_size.get("base_sha"), change_size.get("head_sha")
    if not isinstance(base, str) or not base or not isinstance(head, str) or not head:
        raise ValueError(f"Baseline for '{plan}/{task}' has no complete change_size commit range (base_sha/head_sha)")
    protocol_path = root / ".snodo" / "protocol.yml"
    try:
        protocol = yaml.safe_load(protocol_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not read protocol at {protocol_path}: {exc}") from exc
    phase = next((item.get("evaluation_phase") for item in protocol.get("validators", [])
                  if isinstance(item, dict) and item.get("validator_id") == validator_id), None)
    if not phase:
        raise ValueError(f"Validator '{validator_id}' is not defined in {protocol_path}")
    return ValidatorReplayInput(
        spec=_task_spec(root, plan, task), phase=phase,
        base_commit=base, head_commit=base if phase == "pre_execute" else head,
        result={key: result[key] for key in ("validator_id", "severity", "justification", "elapsed_ms", "time_ms", "usage", "token_usage") if key in result},
    )
