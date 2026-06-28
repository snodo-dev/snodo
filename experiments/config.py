"""Experiment configuration loader + validator (Hydra-ready).

Loads experiments/config.yml, validates types/ranges, applies CLI overrides
from `--set section.key=value` flags, and returns a dict suitable for
snapshotting verbatim into a run's results directory.

Sections map 1:1 onto Hydra config groups so a later switch to Hydra is
drop-in — no hydra dependency required now.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yml"

# Validation constraints: (type_fn, min, max) per dotted key
_CONSTRAINTS: Dict[str, tuple] = {
    "selection.n": (int, 1, 10_000),
    "selection.min_repos": (int, 1, 1_000),
    "selection.seed": (int, 0, 2**31 - 1),
    "sampling.temperature": (float, 0.0, 2.0),
    "sampling.k_trials": (int, 1, 100),
    "bounds.max_recovery_depth": (int, 0, 20),
    "bounds.max_total_fix_attempts": (int, 1, 100),
    "stats.equivalence_margin_pp": (int, 1, 50),
    "stats.min_meaningful_effect_pp": (int, 1, 50),
}


def _parse_json_value(raw: str):
    """Parse a CLI override value that looks like JSON (dict or list).

    Falls back to raw string if parsing fails.
    """
    stripped = raw.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return raw


def _load_yaml(path: Path) -> dict:
    """Load a YAML file. Uses stdlib json for .json, pyyaml for .yml."""
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def _set_nested(d: dict, key: str, value: Any) -> None:
    """Set a dotted key like 'selection.n' on a nested dict."""
    parts = key.split(".", 1)
    if len(parts) == 1:
        d[parts[0]] = value
    else:
        if parts[0] not in d:
            d[parts[0]] = {}
        _set_nested(d[parts[0]], parts[1], value)


def _get_nested(d: dict, key: str) -> Any:
    """Get a dotted key like 'selection.n' from a nested dict."""
    parts = key.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict):
            raise KeyError(key)
        cur = cur[p]
    return cur


def _coerce_value(raw: str, type_fn) -> Any:
    """Coerce a CLI override string to the expected type."""
    if type_fn is int:
        return int(raw)
    elif type_fn is float:
        return float(raw)
    return raw


def _validate_strata(config: dict) -> None:
    """Validate selection.strata: list (proportional) or dict (explicit counts).

    Raises ValueError on failure.
    """
    sel = config.get("selection", {})
    strata = sel.get("strata")
    n = sel.get("n")
    if isinstance(strata, dict):
        valid_keys = {"easy", "medium", "hard"}
        for key in strata:
            if key not in valid_keys:
                raise ValueError(
                    f"selection.strata key {key!r} is not valid. "
                    f"Must be one of {sorted(valid_keys)}"
                )
        for key, count in strata.items():
            if not isinstance(count, int) or count < 0:
                raise ValueError(
                    f"selection.strata.{key}: expected non-negative int, "
                    f"got {count!r}"
                )
        total = sum(strata.values())
        if total != n:
            raise ValueError(
                f"selection.strata counts sum to {total}, but "
                f"selection.n={n}"
            )


def _validate(config: dict) -> None:
    """Validate all constrained keys in-place. Raises ValueError on failure."""
    for key, (type_fn, lo, hi) in _CONSTRAINTS.items():
        try:
            val = _get_nested(config, key)
        except KeyError:
            raise ValueError(f"Missing required config key: {key}")
        if not isinstance(val, type_fn):
            raise ValueError(
                f"{key}: expected {type_fn.__name__}, got {type(val).__name__} ({val!r})"
            )
        if lo is not None and val < lo:
            raise ValueError(
                f"{key}={val} is below minimum {lo}"
            )
        if hi is not None and val > hi:
            raise ValueError(
                f"{key}={val} exceeds maximum {hi}"
            )
    _validate_strata(config)


def load_config(
    path: Optional[Path] = None,
    cli_overrides: Optional[list] = None,
) -> dict:
    """Load, validate, apply CLI overrides, and return experiment config.

    Args:
        path: Path to config.yml (defaults to experiments/config.yml).
        cli_overrides: List of ``key=value`` strings (parsed from ``--set``).

    Returns:
        Deep-copied config dict ready for snapshotting.

    Raises:
        FileNotFoundError: Config file not found.
        ValueError: Validation failure.
    """
    path = path or _CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Experiment config not found: {path}")

    config = _load_yaml(path)
    if not isinstance(config, dict):
        raise ValueError(f"Config root must be a dict, got {type(config).__name__}")

    # Apply CLI overrides
    if cli_overrides:
        for override in cli_overrides:
            if "=" not in override:
                raise ValueError(
                    f"Invalid --set format: {override!r}  (expected key=value)"
                )
            key, raw_value = override.split("=", 1)
            # Try to infer type from existing value for coercion
            try:
                existing = _get_nested(config, key)
                if isinstance(existing, bool):
                    value = raw_value.lower() in ("true", "1", "yes")
                elif isinstance(existing, int):
                    value = int(raw_value)
                elif isinstance(existing, float):
                    value = float(raw_value)
                else:
                    value = _parse_json_value(raw_value)
            except (KeyError, ValueError):
                value = _parse_json_value(raw_value)
            _set_nested(config, key, value)

    _validate(config)
    return copy.deepcopy(config)


def snapshot_path(run_dir: Path) -> Path:
    """Return the path where the config snapshot should be written."""
    return run_dir / "experiment_config.yml"


def write_snapshot(run_dir: Path, config: dict) -> Path:
    """Write a verbatim copy of the resolved config into *run_dir*.

    The snapshot is YAML (diffable against the committed config.yml).
    """
    import yaml
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = snapshot_path(run_dir)
    with open(dest, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    return dest


def format_config(config: dict) -> str:
    """Return a human-readable / machine-readable representation."""
    return json.dumps(config, indent=2, default=str)
