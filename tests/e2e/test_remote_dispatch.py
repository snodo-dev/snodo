"""Real plan dispatch through a local fake-SSH ADR 055 worker."""

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _fake_ssh(path, host_path, *, kill_worker=False):
    script = path / "ssh"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, shlex, subprocess, sys\n"
        "command = sys.argv[-1]\n"
        "host_path = " + repr(str(host_path)) + "\n"
        "if command == 'true': sys.exit(0)\n"
        "if command == 'snodo --version':\n"
        "    sys.exit(subprocess.call(['snodo', '--version']))\n"
        "for verb in ('git-receive-pack', 'git-upload-pack'):\n"
        "    if command.startswith(verb + ' '):\n"
        "        sys.exit(subprocess.call(['git', verb.removeprefix('git-'), shlex.split(command)[1]]))\n"
        "command = command.replace(shlex.quote(host_path), shlex.quote(host_path))\n"
        + (
            "if 'snodo _worker ' in command:\n"
            "    proc = subprocess.Popen(command, shell=True, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)\n"
            "    for line in proc.stdout:\n"
            "        sys.stdout.write(line); sys.stdout.flush()\n"
            "        if '\"kind\":\"log\"' in line:\n"
            "            proc.kill(); proc.wait(); sys.exit(255)\n"
            "    sys.exit(proc.wait())\n"
            if kill_worker else ""
        )
        + "sys.exit(subprocess.call(command, shell=True))\n"
    )
    script.chmod(0o755)
    return script


def _setup_remote_plan(snodo_cli, tmp_path, *, task_count=2):
    assert snodo_cli(["init", "--template", "solo", "--yes"]).returncode == 0
    project = snodo_cli.home
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git("remote", "add", "origin", str(bare), cwd=project)
    protocol_path = project / ".snodo" / "protocol.yml"
    protocol_text = protocol_path.read_text().replace(
        "  auto_merge: true", f"  auto_merge: true\n  host: worker\n  host_path: {project.parent / 'remote'}",
    )
    protocol_path.write_text(protocol_text)
    plan_dir = project / ".snodo" / "plans" / "remote-plan"
    tasks = [f"1.{i}_remote" for i in range(1, task_count + 1)]
    wave_dir = plan_dir / "wave_1"
    wave_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(
        "intent: remote dispatch\nname: remote-plan\nwaves:\n  - id: 1\n    tasks:\n"
        + "".join(f"      - {task}\n" for task in tasks)
    )
    (plan_dir / "status.json").write_text(json.dumps({"tasks": {}}))
    for task in tasks:
        (wave_dir / f"{task}_task.md").write_text(f"Create a small artifact for {task}.\n")
    _git("add", ".", cwd=project)
    _git("commit", "-m", "remote plan fixture", cwd=project)
    _git("push", "-u", "origin", "HEAD", cwd=project)
    remote = project.parent / "remote"
    subprocess.run(["git", "clone", "-q", str(bare), str(remote)], check=True)
    return project, remote, tasks


def _invoke(snodo_cli, args, bin_dir, *, host_path, kill_worker=False):
    env = os.environ.copy()
    env.update({
        "SNODO_HOME": str(snodo_cli.snodo_home),
        "SNODO_TOKEN_SECRET": "e2e_test_fixed_secret_32bytes!",
        "OPENAI_API_KEY": "e2e-openai-key-not-real",
        "ANTHROPIC_API_KEY": "e2e-anthropic-key-not-real",
        "SNODO_HOST": "worker",
        "PATH": f"{bin_dir}{os.pathsep}{os.path.dirname(sys.executable)}{os.pathsep}{os.environ['PATH']}",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("SNODO_AUDIT_LOG", None)
    env["FAKE_SSH_KILL_WORKER"] = "1" if kill_worker else "0"
    if args and args[0] == "plan":
        env["SNODO_JOB_ID"] = "j_remote_e2e"
        env["SNODO_PLAN_JOB"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "snodo", *args], cwd=snodo_cli.home, env=env,
        capture_output=True, text=True,
    )


def test_remote_plan_dispatch_merges_locally_and_records_worker_stream(snodo_cli, tmp_path):
    project, remote, tasks = _setup_remote_plan(snodo_cli, tmp_path, task_count=1)
    job_dir = project / ".snodo" / "jobs" / "j_remote_e2e"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(json.dumps({"status": "running", "created_at": 1}))
    (job_dir / "task.json").write_text(json.dumps({"plan_name": "remote-plan"}))
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True, check=True).stdout.strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_ssh(bin_dir, remote)

    result = _invoke(snodo_cli, ["plan", "run", "remote-plan", "--mock"], bin_dir, host_path=remote)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads((project / ".snodo" / "plans" / "remote-plan" / "status.json").read_text())
    assert status["tasks"][tasks[0]] == "completed"
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True, check=True).stdout.strip() != before
    from snodo.infrastructure.audit import AuditLog
    assert AuditLog(str(project / ".snodo" / "audit.log")).verify_chain()
    from snodo.jobs import JobManager
    manager = JobManager(str(project))
    assert manager.get_status("j_remote_e2e")["task"]["host"] == "worker"
    assert manager.list_jobs()[0]["host"] == "worker"
    assert manager.get_logs("j_remote_e2e")
    for secret in ("e2e-openai-key-not-real", "e2e-anthropic-key-not-real"):
        assert all(secret not in file.read_text(errors="replace")
                   for file in (project / ".snodo" / "jobs").rglob("*") if file.is_file())


def test_remote_ssh_dying_mid_task_marks_plan_task_errored(snodo_cli, tmp_path):
    project, remote, tasks = _setup_remote_plan(snodo_cli, tmp_path, task_count=1)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_ssh(bin_dir, remote, kill_worker=True)
    result = _invoke(snodo_cli, ["plan", "run", "remote-plan", "--mock"], bin_dir, host_path=remote, kill_worker=True)
    assert result.returncode == 1, result.stdout + result.stderr
    status = json.loads((project / ".snodo" / "plans" / "remote-plan" / "status.json").read_text())
    entry = status["tasks"][tasks[0]]
    assert (entry.get("status") if isinstance(entry, dict) else entry) == "errored"
