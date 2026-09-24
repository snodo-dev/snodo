# Command Reference

This is the operator's index of the commands shipped by `snodo`. The command
descriptions and argument names below follow the CLI help. Run any command with
`--help` for its complete option list.

## Start And Configure

Use these commands to create a project, select its operating context, and set
up providers or cloud sync.

| Command | Purpose |
|---|---|
| `snodo init` | Initialize the Snodo project structure. |
| `snodo config add` | Store an API key for a provider. |
| `snodo config get` | Get a configuration value. |
| `snodo config remove` | Remove a provider API key. |
| `snodo config set` | Set a configuration value. |
| `snodo config show` | Show configured keys in masked form. |
| `snodo config test` | Validate all configured keys. |
| `snodo mode show` | Show the current active protocol mode. |
| `snodo mode change` | Change the active protocol mode. |
| `snodo session list` | List sessions, optionally filtered by mode, project, or status. |
| `snodo session show` | Show details for a session. |
| `snodo session new` | Create a new session and set it as active. |
| `snodo session switch` | Set an existing session as active. |
| `snodo session delete` | Delete a session. |
| `snodo session prune` | Remove stale sessions. |
| `snodo models` | List configured providers and their models. |
| `snodo cloud connect` | Connect to Snodo Cloud and enable audit sync. |
| `snodo cloud disconnect` | Disconnect from Snodo Cloud and disable sync. |
| `snodo cloud status` | Show cloud connection and sync status. |
| `snodo cloud sync` | Ship unsynced audit events to Snodo Cloud. |
| `snodo cloud schema` | Publish the versioned JSON schemas for the cloud wire interface. |
| `snodo install` | Install MCP servers into the Claude Desktop configuration. |
| `snodo uninstall` | Remove Snodo MCP servers from the Claude Desktop configuration. |

Useful forms:

```text
snodo init --template solo
snodo config set engine.max_subtask_depth 3
snodo mode show --json
snodo cloud sync --all
snodo cloud schema --json
```

The schema command derives its two payload schemas from the engine's declared
wire types at the installed version. The interface version is top-level because
it applies to both the PUT snapshot and POST event batch; neither payload has
to carry a version field that its own declared type does not know about.

`snodo cloud connect` takes an API key as a required argument. Do not put a
real key in shell history when a safer secret-handling method is available.

## Author And Plan Work

Use these commands to inspect the repository, create a plan, and execute work.

| Command | Purpose |
|---|---|
| `snodo plan list` | List all plans. |
| `snodo plan status` | Show progress for a named plan. |
| `snodo plan create` | Create a new plan from an intent description. |
| `snodo plan validate` | Validate a plan's structure and spec files. |
| `snodo plan add-task` | Add a task to a plan from a spec file. |
| `snodo plan add-wave` | Add a wave to a plan. |
| `snodo plan run` | Execute a plan's tasks through the protocol loop. |
| `snodo queue run` | Run queued plans until each selected queue is empty or blocked. |
| `snodo plan delete` | Delete a plan directory. |
| `snodo run` | Execute a task through the protocol. |
| `snodo validate` | Run a phase's validators and return the structured result. |
| `snodo ready` | Assess method scaffolding readiness against the configured protocol. |
| `snodo recon` | Dispatch a read-only exploration query to one or more agents. |
| `snodo survey` | Analyze the repository by proposing governance or reporting protocol drift. |
| `snodo intake` | Propose validator criteria from decision records and accept them one at a time. |
| `snodo authorize` | Authorize a pending decision; this is human-only and requires a private signing key. |

Plan and task arguments are positional. For example, a plan can be created,
validated, and run as follows:

```text
snodo plan create "Add a health endpoint" --name health-endpoint --mock
snodo plan validate health-endpoint
snodo plan run health-endpoint --mock
```
`snodo run` accepts a description unless `--plan` is used. Its `--retry`
option takes a task ID; use `--append-spec` to add guidance or
`--replace-spec` to deliberately replace the existing spec.

`snodo queue run` runs the `default` queue, or a named queue such as
`snodo queue run build`. Pass comma-separated names to run queues in parallel;
`--all` runs queues sequentially in creation order. Queue runs stop at the
first blocked, errored, or unmerged plan unless `--non-blocking` is set. With
non-blocking enabled, `--parallel-run N` runs up to N plans from one queue at
once. The protocol's `queue.non_blocking` and `queue.parallel_runs` settings
provide the defaults.

## Follow A Run

Use these commands while work is running or when you need a compact status
view.

| Command | Purpose |
|---|---|
| `snodo status` | Show the protocol, active mode, active session, and most recent run. |
| `snodo logs` | Show output for a job or recon by ID. |
| `snodo job list` | List all background jobs. |
| `snodo job status` | Show the status of a background job. |
| `snodo job logs` | Show logs for a background job. |
| `snodo job wait` | Wait for a background job to complete. |
| `snodo task list` | List all task branches in the current project. |
| `snodo task show` | Inspect a task's halt and failure record from the active session. |
| `snodo task review` | Record an operator review verdict, report acceptance statistics, or list pending reviews. |
| `snodo task report` | Report the operator review acceptance rate over a time window. |
| `snodo task complete` | Record that a task was completed by hand outside the loop. |
| `snodo meta` | Show a compact summary for a job or task. |
| `snodo dashboard` | Launch the TUI dashboard. |
| `snodo agent list` | List all agents. |
| `snodo agent memory` | Show an agent memory summary. |

For a live job, pass the ID returned by `snodo job list` to either the focused
`snodo job logs` command or the general `snodo logs` command. Both commands
also support watching output; the focused form additionally accepts `--tail`
and `--stream`.

## Clean Up And Recover

These commands remove stale state, recover work, or inspect integrity after a
run.

| Command | Purpose |
|---|---|
| `snodo task abandon` | Delete a task branch and clear its failure context. |
| `snodo task prune` | List and delete stale task branches. |
| `snodo job cancel` | Cancel a running job. |
| `snodo job retry` | Retry the task associated with a failed job, keeping its spec by default. |
| `snodo job archive` | Archive old terminal jobs to `.snodo/jobs_archive/`. |
| `snodo job unarchive` | Restore jobs from `.snodo/jobs_archive/`. |
| `snodo job prune` | Permanently delete old terminal jobs. |
| `snodo cache clear` | Delete the verdict cache so the next run judges fresh. |
| `snodo audit verify` | Verify the audit log's hash chain for tamper evidence. |
| `snodo worktree list` | List retained task worktrees. |
| `snodo worktree remove` | Remove a retained task worktree and its branch. |
| `snodo worktree prune` | Remove retained worktrees older than the configured TTL. |
| `snodo agent reset` | Clear agent memory and assign a new thread. |
| `snodo agent rotate` | Rotate an agent thread ID while keeping old checkpoints. |

The destructive cleanup commands prompt unless their help documents a
confirmation bypass such as `--yes` or `--force`. Review the target first:

```text
snodo worktree list
snodo worktree prune --dry-run
snodo audit verify --json
```

## Run Infrastructure

These commands operate the MCP server rather than a single task.

| Command | Purpose |
|---|---|
| `snodo serve` | Start the MCP server from the protocol definition. |

`snodo serve` defaults to stdio. It can also serve SSE or streamable HTTP with
`--transport`, and its help describes tunnel provisioning and Claude Desktop
installation options.

## Three Safe Checks

These commands do not start a task or change project state. After
`snodo init`, paste them from the project directory to inspect the checkout:

```shell
snodo status --json
snodo runs --json
snodo plan list
snodo task list
```

Use `snodo <command> --help` for the full, live option and argument reference.
