"""Requested-versus-served model provenance reporting."""

import json
from pathlib import Path

from snodo.infrastructure.paths import resolve_project_root


def _collect_model_provenance(project_root: Path, limit: int) -> list[dict]:
    """Return recent requested/served pairs without interpreting other fields."""
    jobs_dir = project_root / ".snodo" / "jobs"
    records = []
    if not jobs_dir.is_dir():
        return records

    try:
        job_dirs = [entry for entry in jobs_dir.iterdir() if entry.is_dir()]
    except OSError:
        return records

    for job_dir in job_dirs:
        try:
            state = json.loads((job_dir / "state.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue

        for record_type in ("usage", "tool_telemetry"):
            source_records = state.get(record_type)
            if not isinstance(source_records, list):
                continue
            for record in source_records:
                if not isinstance(record, dict):
                    continue
                requested = record.get("model")
                if not isinstance(requested, str) or not requested.strip():
                    continue
                served = record.get("served_model")
                if isinstance(served, str):
                    served = served.strip() or None
                else:
                    served = None
                if served is None:
                    comparison = "unreported"
                elif served == requested:
                    comparison = "match"
                else:
                    comparison = "mismatch"
                records.append({
                    "run": job_dir.name,
                    "record_type": record_type,
                    "timestamp": record.get("timestamp"),
                    "role": record.get("role"),
                    "requested": requested,
                    "served": served,
                    "comparison": comparison,
                })

    records.sort(
        key=lambda record: record["timestamp"]
        if isinstance(record["timestamp"], (int, float))
        else float("-inf"),
        reverse=True,
    )
    return records[:limit]


def models_provenance_command(args) -> int:
    """Show directly recorded requested and provider-served model names."""
    root = resolve_project_root()
    if root is None:
        cwd = Path.cwd()
        if (cwd / ".snodo").is_dir():
            root = str(cwd)

    limit = getattr(args, "provenance_limit", 20)
    records = _collect_model_provenance(Path(root), limit) if root is not None else []
    if getattr(args, "json", False):
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("models-provenance"),
            "ok": True,
            "project_root": root,
            "records": records,
        })

    print("Model provenance (requested -> served):")
    if not records:
        print("  No model provenance recorded.")
        return 0
    print("  RUN  TYPE             ROLE       REQUESTED                 SERVED                    COMPARISON")
    for record in records:
        print(
            f"  {record['run']}  {record['record_type']:<16} "
            f"{str(record['role'] or '-'):<10} {record['requested']:<25} "
            f"{str(record['served'] or 'unreported'):<25} {record['comparison']}"
        )
    return 0
