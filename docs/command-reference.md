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
| `snodo config add` | `<provider> <key>` |
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
| `snodo install` | `--protocol` |
| `snodo uninstall` | `--mode`, `--all`, `--purge`, `--orphans`, `--yes`/`-y` |

`snodo config set/get` take dotted configuration keys (for example
`llm.coder.max_tokens`). Only the top-level `model`, `engine.*`, and `llm.*`
keys are supported by the setter; edit other settings, including cloud and
notification targets, in `~/.snodo/config.yml`. See the
[configuration reference](configuration.md). `snodo cloud schema --json`
prints the generated cloud interface schema. `snodo config --notify-test` sends
a test to every valid notification target.

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
| `snodo readiness` | `--mode`/`-m`, `--protocol`, `--json` (legacy alias) |
| `snodo recon` | `<query> <paths>`, `--agents`/`-n` |
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
The hidden `snodo notify test` command remains as a deprecated alias for one
release and prints a migration notice.

## MCP server

| Command | Arguments and options |
|---|---|
| `snodo serve` | `--protocol`, `--mode`, `--transport`, `--port`, `--tunnel`, `--verbose`, `--auth`, `--rotate`, `--credential`, `--delete`, `--hostname`, `--mcp-install`, `--mcp-uninstall`, `--mcp-uninstall-all`, `--mcp-list`, `--purge`, `--orphans`, `--yes`/`-y`, deprecated `--install`, `--uninstall`, `--uninstall-all`, `--project-name` |

`snodo serve --verbose` logs timestamped, redacted and bounded MCP request and
response summaries; authentication refusals identify the failed check. The
server defaults to stdio. HTTP transports require `--transport sse` or
`--transport streamable-http`; `--tunnel` provisions and starts a managed
tunnel. See `snodo serve --help` for tunnel auth, rotation, deletion, and
Claude Desktop management options.

Without `--mode`, the server exposes the current mode from
`.snodo/state.json`, falling back to the protocol's `initial_mode`; `--mode`
pins it to a named mode. It does not combine grants from other modes. The
`--mcp-install` option registers one entry per mode, each started with its
matching `--mode` pin.
