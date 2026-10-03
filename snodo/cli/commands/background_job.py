"""Background job submission for the snodo run command."""

import sys
from pathlib import Path

from snodo.config import ConfigManager, provider_env
from snodo.cli.commands import followup


def _submit_background_job(args) -> int:
    """Submit task as a background job.

    Validates args, builds task_args dict, calls JobManager.submit(),
    prints job_id with helper commands.
    """
    from snodo.jobs import JobManager, JobError
    from snodo.infrastructure.paths import require_project_root
    from snodo.infrastructure.state import read_state
    from snodo.coders import resolve_coder_name
    from snodo.cli.commands import load_protocol

    if getattr(args, "plan", None):
        print("Error: --plan and --background cannot be used together", file=sys.stderr)
        return 1

    if args.description is None:
        print("Error: task description required for background jobs", file=sys.stderr)
        return 1

    protocol_path = Path(args.protocol)
    if not protocol_path.exists():
        print(f"Error: Protocol file not found: {protocol_path}", file=sys.stderr)
        print("Run 'snodo init' to create default protocol.", file=sys.stderr)
        return 1

    # Set API key env vars so child process inherits them
    mgr = ConfigManager()
    model = args.model or mgr.get_coder_model()

    with provider_env(model) as mgr:
        project_root = require_project_root()
        state = read_state(project_root)
        mode = getattr(args, "mode", None) or state.current_mode or "producer"

        protocol_obj = load_protocol(protocol_path)
        mode_coder = None
        if protocol_obj:
            initial_mode_obj = protocol_obj.get_mode(mode) or protocol_obj.get_mode(protocol_obj.initial_mode)
            mode_coder = getattr(initial_mode_obj, "coder", None) if initial_mode_obj else None
        coder = resolve_coder_name(
            model=model,
            mode_coder=mode_coder,
            cli_coder=getattr(args, "coder", None),
            use_mock=getattr(args, "mock", False),
        )

        task_args = {
            "description": args.description,
            "protocol": args.protocol,
            "model": model,
            "coder": coder,
            "mode": mode,
            "mock": getattr(args, "mock", False),
            "verbose": getattr(args, "verbose", False),
            "from_pr": getattr(args, "from_pr", None),
            "cwd": project_root,
        }

        try:
            manager = JobManager(project_root)
            job_id = manager.submit(task_args)
        except (ValueError, JobError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        print(f"Job submitted: {job_id}")
        # The dashboard covers status/progress; the terminal command tails output.
        status = manager.get_status(job_id)
        task = status.get("task") if isinstance(status.get("task"), dict) else {}
        task_ref = task.get("task_id") or task.get("retry_task_id")
        cloud_config = mgr.load()
        from snodo.infrastructure.cloud_credentials import cloud_sync_enabled
        if task_ref and cloud_sync_enabled(cloud_config):
            from snodo.config import get_cloud_liveness_url
            print(
                f"  Cloud live view: {followup.job_live_view(task_ref, get_cloud_liveness_url(cloud_config))} "
                "(task status and progress; not a live output stream)"
            )
        print(
            f"  Watch (running): {followup.job_followup(job_id, running=True)} "
            "— streams the job's live output in a terminal."
        )
        print(f"  snodo job status {job_id}")
        print(f"  snodo job logs {job_id}")
        print(f"  snodo job wait {job_id}")
        print(f"  snodo meta {job_id}")
        return 0
