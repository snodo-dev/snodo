# Command reference

This index matches the Typer command registrations. `snodo <command> --help`
shows each command's argument descriptions and defaults. `--help` is available
on all commands; top-level options are `--version`, `--verbose`/`-v`,
`--install-completion`, and `--show-completion`.

## Project, configuration, and services

| Command | Arguments and options |
|---|---|
| `snodo init` | `--template`/`-t`, `--force`/`-f`, `--mode`/`-m`, `--project-id`, `--force-keygen`, `--yes`/`-y`, `--no-input`, `--test-command`/`-c` |
| `snodo config show` | — |
| `snodo config add` | `<provider> [key]`, `--ref env:NAME|command:COMMAND` (choose key or reference) |
| `snodo config remove` | `<provider>` |
| `snodo config test` | — |
| `snodo config set` | `<key> <value>` |
| `snodo config get` | `<key>` |
| `snodo config` (group options) | `--encrypt-provider-keys`, `--notify-test` |
| `snodo mode show` | `--json` |
| `snodo mode change` | `<new_mode>` |
| `snodo session list` | `--mode`, `--project`, `--status` |
| `snodo session show` | `<session_id>`, `--json` |
| `snodo session new` | `--mode`, `--yes`/`-y`, `--force`/`-f` |
| `snodo session switch` | `<session_id>` |
| `snodo session delete` | `<session_id>` |
| `snodo session prune` | `--days` |
| `snodo models` | `--provider`/`-p`, `--flush`, `--stats`, `--provenance`, `--provenance-limit`, `--check`, `--benchmark`, `--benchmark-runs`, `--set-baseline`, `--compare`, `--benchmark-run`, `--model`, `--plan`, `--task`/`--task-id`, `--job`, `--json`, `--id`, `--id-contains`, `--max-output-cost`, `--min-output-cost`, `--max-input-cost`, `--min-context` |
| `snodo cloud connect` | `<api_key>` |
| `snodo cloud disconnect` | — |
| `snodo cloud status` | — |
| `snodo cloud sync` | `--all`, `--session`, `--force`/`--retry` |
| `snodo cloud schema` | `--json` |
| `snodo protocol schema` | `--json` |
| `snodo install` | `--protocol` |
| `snodo uninstall` | `--mode`, `--all`, `--purge`, `--orphans`, `--yes`/`-y` |

`snodo config set/get` take dotted configuration keys (for example
`llm.coder.max_tokens`). Only the top-level `model`, `engine.*`, and `llm.*`
keys are supported by the setter; edit other settings, including cloud and
notification targets, in `~/.snodo/config.yml`. See the
[configuration reference](configuration.md). `snodo cloud schema --json`
prints the generated cloud interface schema. `snodo config --notify-test` sends
a test to every valid notification target.
`snodo protocol schema --json` prints the generated, versioned JSON Schema for
protocol authoring without reading the project's protocol file.

`snodo init` creates `.snodo/protocol.yml` and project state from a shipped
protocol template. `--template` selects a shipped protocol by name: `solo`
(Solo Developer), `team` (Default Snodo), `2+n` (2+N Reference), `intent`
(Intent-Driven), `greenfield` (Greenfield Project), `bugfix-surgeon` (Bug-Fix
Surgeon), or `feature-warden` (Feature-Development Warden); omit it for the
interactive picker.
`--test-command` sets the quality validator's test command when the template
value is empty or a placeholder; otherwise init tries detection from project
marker files and may prompt on an interactive terminal. `--project-id`
overrides detected/configured identity and caches it with override scope.
`--mode` selects the starting mode rather than prompting. `--force-keygen`
regenerates the RS256 signing keypair; `--force` permits overwriting an existing
`.snodo/` directory or initializing within a Snodo project. `--yes` and
`--no-input` acknowledge the trusted-repository warning without prompting,
including in non-interactive use.

## Work and planning

| Command | Arguments and options |
|---|---|
| `snodo run` | `[description]`; `--protocol`, `--model`/`-m`, `--coder`, `--mode`, `--module`, `--verbose`, `--mock`, `--plan`/`-p`, `--wave`/`-w`, `--interactive`/`-i`, `--from-pr`, `--background`/`-b`, `--resume`, `--retry`, `--append-spec`, `--replace-spec`, `--retain-worktree`, `--no-isolation`, `--fixture` |
| `snodo plan list` | `--json`, `--tree` |
| `snodo plan status` | `<name>` |
| `snodo plan create` | `<description>`, `--name`/`-n`, `--protocol`, `--model`/`-m`, `--mock` |
| `snodo plan validate` | `<name>`, `--json`, `--protocol` |
| `snodo plan run` | `<name>`, `--wave`/`-w`, `--interactive`/`-i`, `--protocol`, `--model`/`-m`, `--coder`, `--mode`, `--module`, `--verbose`, `--mock`, `--retain-worktree`, `--no-isolation`, `--fixture` |
| `snodo plan add-task` | `<plan> <task_id>`, `--spec-file` (required), `--parent`, `--replace`, `--module` |
| `snodo plan add-wave` | `<plan> <id>`, `--depends-on` |
| `snodo plan delete` | `<name>`, `--force` |
| `snodo queue create` | `<name>`, `--json` |
| `snodo queue move` | `<plan>`, `--front`, `--before`, `--after`, `--to`, `--json` |
| `snodo queue remove` | `<plan>`, `--json` |
| `snodo queue run` | `[queues]`, `--all`, `--non-blocking`, `--parallel-run`, `--protocol`, `--mock` |
| `snodo queue validate` | `[queue]`, `--json` |
| `snodo queue` (group option) | `--json` |
| `snodo validate` | `<task_spec>`, `--phase`, `--protocol`, `--mode`, `--json` |
| `snodo ready` | `--mode`/`-m`, `--protocol`, `--json` |
| `snodo readiness` | `--mode`/`-m`, `--protocol`, `--json` (deprecated alias for `ready`) |
| `snodo recon` | `<query> [paths...]`, `--agents`/`-n` |
| `snodo survey` | `--json`, `--agent` |
| `snodo intake` | `--validator`, `--json`, `--accept-all`, `--reject-all`, `--no-input` |
| `snodo authorize` | `[task_id]`, `--yes`/`-y`, `--reject-all` |

`--module` names a declared protocol module for a task; it selects that
module's test command and bounds writable paths. Plans with no tasks across all
waves are refused. `snodo plan create` makes an authoring scaffold, not a
generated or runnable plan. A task can be run directly; waves group parallel
tasks, plans order multiple waves, and queues schedule multiple plans. See
[Authoring a plan](authoring-a-plan.md) and the canonical
[queue guide](queues.md).

`--fixture` is used only when `snodo run --plan` is supplied; it is ignored for
a direct `snodo run` task. Candidate benchmark results are compared with a
stored task baseline through `snodo models --compare` (with `--plan` and
`--task`).

`snodo recon <query> [paths...]` runs read-only codebase exploration, waits up
to 300 seconds, and prints each agent's answer. `--agents`/`-n` requests the
number of agent lanes; if omitted, the configured count is used. Configured
models supply the lanes in order, and failed model attempts are reported to
stderr. See [CLI recon](decompose-and-recon.md#cli-recon).

## Jobs, status, and cleanup

| Command | Arguments and options |
|---|---|
| `snodo status` | `--json` |
| `snodo logs` | `<composite_id>`, `--watch`/`-w` |
| `snodo runs` | `--json`, `--send` |
| `snodo meta` | `<composite_id>`, `--json` |
| `snodo job list` | — |
| `snodo job status` | `<job_id>` |
| `snodo job logs` | `<job_id>`, `--stream`/`-s`, `--tail`/`-n`, `--watch`/`-w` |
| `snodo job wait` | `<job_id>`, `--timeout`/`-t` |
| `snodo job cancel` | `<job_id>` |
| `snodo job archive` | `--days`, `--yes`/`-y` |
| `snodo job prune` | `--days`, `--yes`/`-y` |
| `snodo job unarchive` | `--days`, `--yes`/`-y` |
| `snodo job retry` | `<job_id>`, `[description]`, `--replace-spec` |
| `snodo task list` | — |
| `snodo task show` | `<task_id>`, `--json` |
| `snodo task abandon` | `<task_id>` |
| `snodo task prune` | `--days`/`--stale-days` |
| `snodo task review` | `<task_id> <verdict>`, `--notes`, `--report`, `--pending`, `--days`, `--json` |
| `snodo task report` | `--days`, `--json` |
| `snodo task complete` | `<task_id>`, `--plan`/`-p`, `--who`/`--by`, `--notes`, `--json` |
| `snodo worktree list` | `--json` |
| `snodo worktree remove` | `<task_id>` |
| `snodo worktree prune` | `--days`, `--force`/`-f`, `--dry-run` |
| `snodo audit verify` | `--json` |
| `snodo cache clear` | `--json` |
| `snodo agent list` | — |
| `snodo agent memory` | `<agent_id>` |
| `snodo agent reset` | `<agent_id>` |
| `snodo agent rotate` | `<agent_id>` |
| `snodo dashboard` | — |

For a live job, the browser watch link (usually through the configured managed
tunnel) streams status and recent output. The CLI alternative is
`snodo logs <job_id> --watch`; `snodo job logs <job_id> --watch` is also
available. `watch_job` is an optional MCP Apps enhancement and returns a
plain-text snapshot to hosts that do not render Apps. Job notifications are
configured in `~/.snodo/config.yml` and tested with `snodo config --notify-test`.
The hidden `snodo notify test` command is deprecated in v0.18.0 and prints a
migration notice; use `snodo config --notify-test` instead. `snodo readiness`
is likewise a deprecated alias for `snodo ready` in v0.18.0.

Use `snodo status` for protocol, active mode/session, and the most recent
session run. Use `snodo ready` to assess protocol method-scaffolding readiness
(`--mode` filters findings, not the whole-protocol score). `snodo runs` lists
completed task-run records; `--send` sends those records to Snodo Cloud.
`snodo task list` lists recorded tasks for inspection and management.

`snodo meta <composite_id>` summarizes a job or task. Job IDs conventionally
start with `j_` and task IDs with `task_`; other values are resolved by checking
the project's job and task record directories.

## MCP server

| Command | Arguments and options |
|---|---|
| `snodo serve` | `--protocol`, `--mode`, `--transport`, `--port`, `--tunnel`, `--verbose`, `--auth`, `--rotate`, `--credential`, `--delete`, `--hostname`, `--mcp-install`, `--mcp-uninstall`, `--mcp-uninstall-all`, `--mcp-list`, `--purge`, `--orphans`, `--yes`/`-y`, deprecated `--install`, `--uninstall`, `--uninstall-all`, `--project-name` |

The legacy `snodo serve --install`, `--uninstall`, and `--uninstall-all`
options are deprecated in v0.18.0; use `--mcp-install`, `--mcp-uninstall`,
and `--mcp-uninstall-all`, respectively. `--project-name` is also deprecated
in v0.18.0.

`snodo serve --verbose` logs timestamped, redacted and bounded MCP request and
response summaries; authentication refusals identify the failed check. The
server defaults to stdio. HTTP transports require `--transport sse` or
`--transport streamable-http`; `--tunnel` provisions and starts a managed
tunnel. See `snodo serve --help` for tunnel auth, rotation, deletion, and
MCP client management options for installed Claude Desktop and Codex-family
clients (ChatGPT desktop, Codex CLI, and the IDE extension share
`~/.codex/config.toml`; `CODEX_HOME` can relocate it).

Without `--mode`, the server exposes the current mode from
`.snodo/state.json`, falling back to the protocol's `initial_mode`; `--mode`
pins it to a named mode. It does not combine grants from other modes. The
`--mcp-install` option registers one entry per mode, each started with its
matching `--mode` pin.
Each entry points to the Snodo installation that performed the registration,
using an absolute executable path. Re-run `snodo serve --mcp-install` after
moving or recreating that virtual environment so clients launch the current
installation.
Installation and cleanup update each supported client whose configuration
directory exists: Claude Desktop and the Codex family (ChatGPT desktop, Codex
CLI, and the IDE extension). Codex stores its shared configuration in
`~/.codex/config.toml`, or `$CODEX_HOME/config.toml` when `CODEX_HOME` is set.
Absent clients are skipped; restart each updated client after installing.
