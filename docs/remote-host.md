<!-- snodo-guide topic="remote-host" summary="Set up and run tasks on an SSH host" section="# Run tasks on a remote host" -->
# Run tasks on a remote host

Use remote execution when the local machine should orchestrate and keep Snodo's
records, while a stronger SSH-reachable machine does the task work. This is
especially useful for heavy parallel queues; the local machine remains the
single writer for plans, queues, audit, status, liveness, cloud sync, and merge.

## Choose the host

Set `execution.host` in `.snodo/protocol.yml` to an SSH host alias or host
string accepted by `ssh`:

```yaml
execution:
  host: GPU2
  host_path: /home/me/Dev/project
```

`SNODO_HOST` overrides `execution.host` for the current process. If neither is
set, tasks run locally. Snodo uses the SSH configuration already available to
the operator; it does not configure SSH itself.

`execution.host_path` is the project clone's path on the host. When omitted,
Snodo uses the local project's path relative to the local home directory under
the remote home directory (for example, local `~/Dev/project` maps to remote
`~/Dev/project`). Set an explicit path if the local project is outside your
home directory or the remote clone lives elsewhere. The path must name a
separate project clone, not a mounted/network-filesystem copy of the local
worktree.

## Set up the host once

1. Install Snodo on the host at exactly the same version as the local
   installation (`snodo --version` reports it). Keep both installations in
   sync when upgrading.
2. Clone the project on the host. Its `origin` must identify the same project
   as the local clone. Ensure the configured `execution.host_path` points to
   this clone; without an explicit path, create it at the derived remote-home
   path.
3. Configure SSH so `ssh <host>` succeeds non-interactively for the operator
   running Snodo. Authentication prompts and host-key confirmation cannot be
   answered during a task.
4. Log in once on the host for any tools that keep their own login state there.
   For example, if the configured coder uses OpenCode's own account store, run
   `opencode auth login` on the host as the same user that runs remote tasks.
   Provider API keys configured in Snodo are handled separately and do not
   need to be copied into a host-side Snodo key store.

After setup, run this from the local project directory:

```shell
snodo host check
```

Add `--json` for machine-readable results. With no selected host it reports
that execution is local. For a selected host, it runs all checks independently:

| Check | What it verifies | If it fails |
|---|---|---|
| `reachable` | SSH can run a command in batch/non-interactive mode. | Check the host alias, network, SSH key/agent, and host-key setup; make sure login needs no prompt. |
| `version` | Remote `snodo --version` exactly matches the local Snodo version. | Install or select the matching version on the host and ensure `snodo` is on the non-interactive SSH command's `PATH`. |
| `project_clone` | The configured host path exists as a git working clone and its `origin` matches the local project's `origin`. | Clone the same project at that path, correct `execution.host_path`, or fix the clone's `origin`. If the local project has no `origin`, configure one before checking. |

Do not start a task until every check passes. A failed preflight refuses remote
execution; Snodo does not silently fall back to local execution.

## What runs where

The complete task loop runs on the host: pre-execute validation, coder,
verification and test commands, and post-execute validation. Snodo pushes the
base commit to the host clone over SSH and creates the task worktree there.
Afterward it fetches the task branch back and performs any merge locally.

The local machine keeps the plans, queues, audit log, task status, liveness,
cloud sync, and merge. The remote worker is stateless: it reports logs, audit
events, status, heartbeats, and its final result through stdout. Snodo reads that
stream locally, so local job logs, audit history, status, and liveness continue
to work as usual. Project files are transferred through git; Snodo does not
mount the local worktree or share `.snodo` state with the host.

## How credentials travel

Snodo resolves configured provider keys on the local machine (including keys
stored through `@keys/`) and sends them in memory over the SSH session to the
remote worker. The worker keeps them in memory and never writes them to the
host's disk. Do not copy Snodo's local key store to the host. A tool's separate
host-side login store, such as OpenCode's, is set up once on the host as
described above.

## When a remote task is `errored`

`errored` means the remote worker could not report a valid completed task: its
stream ended without a final result, SSH exited non-zero, or no stream line
arrived for 60 seconds. This is an operational failure, not a validator verdict
on the task's content and not a new status or halt type. Inspect the job logs
and task details to diagnose SSH, host, tool, or worker problems. Snodo retains
the host worktree so a retry can reuse it.

After fixing the cause, retry the associated failed job with `snodo job retry
<job-id>`. Review the retry's job and task outcomes as usual; do not blindly
retry repeated remote failures.

### Retry through MCP

If the current mode exposes the `retry_job` tool, call it with the failed
`job_id` after fixing the cause. Review the resulting job and task outcomes;
do not blindly retry repeated remote failures.
