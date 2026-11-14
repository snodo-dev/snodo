"""Run the governed and bare scikit-learn demo arms."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo import commands, protocols, record, report, verify, workspace

Runner = Callable[..., Any]
FIXTURE = Path(__file__).parent / "scikit-learn-26194" / "instance.json"


def _task_ids(root: Path) -> list[str]:
    tasks = root / ".snodo" / "tasks"
    return (
        sorted(path.name for path in tasks.iterdir() if path.is_dir())
        if tasks.exists()
        else []
    )


def run_arm(
    model: str,
    arm: str,
    workdir: str | Path,
    *,
    snodo_version: str | None = None,
    coder: str | None = None,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, Any], str]:
    """Run one arm in a fresh clone and persist its record and report."""
    if arm not in {"governed", "bare"}:
        raise ValueError("arm must be governed or bare")
    root = Path(workdir)
    root.mkdir(parents=True, exist_ok=True)
    project = root / f"{arm}-project"
    instance = workspace.load_instance(FIXTURE)
    workspace.clone_instance(instance, project, runner=runner)
    workspace.init_workspace(
        project, commands.command_prefix(snodo_version), runner=runner
    )
    text = (
        protocols.governed_protocol_text()
        if arm == "governed"
        else protocols.bare_protocol_text()
    )
    protocol_path = protocols.write_protocol(text, root / f"{arm}-protocol.yml")
    valid, findings = protocols.validate_protocol(protocol_path)
    if valid is False:
        print(f"Protocol validation failed for {arm}: {findings}", file=sys.stderr)
    elif valid is None:
        print(f"Protocol validation unavailable for {arm}: {findings}", file=sys.stderr)

    before = _task_ids(project)
    run_error: Exception | None = None
    try:
        runner(
            commands.run_command(
                commands.read_task_text(), protocol_path, model, coder, snodo_version
            ),
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        run_error = exc
    after = _task_ids(project)
    try:
        task_id = record.find_new_task_id(before, after)
    except ValueError:
        task_id = "missing-task"
    run_record = record.build_record(project, task_id, model, arm)
    if run_error is not None and run_record["outcome"] == "passed":
        run_record["outcome"] = "unknown"
    patch = report.collect_patch(
        run_record.get("worktree_path"), instance["base_commit"]
    )
    verification = verify.verify_patch(instance, patch)
    run_record["verification"] = verification
    if run_error is not None:
        run_record["error"] = str(run_error)
    record.write_record(run_record, root / f"{arm}.json")
    report_text = report.format_report(run_record, patch, verification)
    (root / f"{arm}.txt").write_text(report_text, encoding="utf-8")
    return run_record, report_text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--arm", choices=("governed", "bare", "both"), default="both")
    parser.add_argument("--workdir")
    parser.add_argument("--snodo-version")
    parser.add_argument("--coder")
    args = parser.parse_args(argv)
    if args.workdir:
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="snodo-demo-"))
    print(f"Demo records: {workdir}")
    arms = ("governed", "bare") if args.arm == "both" else (args.arm,)
    for arm in arms:
        _, text = run_arm(
            args.model,
            arm,
            workdir / arm,
            snodo_version=args.snodo_version,
            coder=args.coder,
        )
        print(text)
    print(f"Records and reports: {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
