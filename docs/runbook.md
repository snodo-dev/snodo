# Snodo Runbook — Install & Operate

## Install

Requires Python 3.12 or later.

```bash
pip install snodo
```

Verify:

```bash
snodo --version
```

From source (a [`uv`](https://docs.astral.sh/uv/) workspace):

```bash
git clone https://github.com/snodo-dev/snodo.git
cd snodo
uv sync --all-extras
```

## Configure

### API keys

Store keys in `~/.snodo/config.yml` (permissions 0600, file created on first `snodo config add`):

```bash
snodo config add openai sk-...
snodo config add anthropic sk-ant-...
snodo config add google  AIza...
```

Or set environment variables — the engine auto-detects based on the model prefix:

| Model prefix | Environment variable |
|-------------|---------------------|
| `claude-*` | `ANTHROPIC_API_KEY` |
| `gpt-*`, `o1-*`, `o3-*` | `OPENAI_API_KEY` |
| `gemini-*`, `gemini/*` | `GEMINI_API_KEY` |

GitHub token for `--from-pr`:

```
export GITHUB_TOKEN=ghp_...
```

### Engine settings

```bash
snodo config set engine.max_subtask_depth 5     # default: 3, range 1-10
snodo config set engine.max_session_age_days 60  # default: 30, range 1-365
snodo config set engine.token_ttl_seconds 1200   # default: 600, range 60-86400
```

### Snodo home directory

Default: `~/.snodo/`. Override with `SNODO_HOME`:

```bash
export SNODO_HOME=/custom/path
```

All data — config, sessions, agent memory — lives under this directory.

### Token secret

JWT validation tokens are HS256-signed. The signing secret is randomly generated per process. To persist across restarts:

```bash
export SNODO_TOKEN_SECRET=$(openssl rand -hex 32)
```

### The full configuration file

Configuration lives in `~/.snodo/config.yml` (`$SNODO_HOME` overrides the
location). The file below is the whole surface, with the defaults and ranges
shown.

```yaml
model: deepseek/deepseek-v4                   # default for all roles

llm:
  num_retries: 3                              # 0-10, litellm retry count
  coder:
    model: null                               # null = the default model
    max_tokens: 16000
    max_tool_turns: 6                         # 1-200
    timeout_seconds: 1800
    concurrency: 1                            # coders this operator can carry
  validator:
    model: null                               # role-specific override
    max_tokens: 1500
    max_tool_turns: 6                         # 1-200
  classifier:
    model: null
    max_tokens: 500
    temperature: 0.0                          # 0.0-2.0
  recon:
    num_agents: 1
    models: []                                # ordered failover list
  wave:
    max_age_days: 14
    max_idle_days: 5

engine:
  max_subtask_depth: 3                        # 1-10
  max_session_age_days: 30                    # 1-365
  token_ttl_seconds: 600                      # 60-86400

providers:
  anthropic:
    api_key: sk-ant-...
    api_key_env: ANTHROPIC_API_KEY            # injected at runtime when a matching model runs
  ollama:
    base_url: https://ollama.com/v1
    api_key_env: OLLAMA_API_KEY
    litellm_provider: openai                  # route ollama/<model> through the OpenAI protocol

cloud:
  api_url: https://api.snodo.dev
  sync_enabled: false
  liveness_interval_seconds: 60             # at most one liveness push per
                                            # interval, and at least one while
                                            # a session is running; lower it to
                                            # hear from a quiet run more often
```

Three shapes of key are settable without hand-editing — the bare key
`model`, anything under `engine.`, and anything under `llm.`:

```bash
snodo config set model deepseek/deepseek-v4
snodo config set llm.coder.max_tokens 32000
snodo config set llm.validator.model openai/@cf/google/gemma-4
snodo config set llm.recon.num_agents 2
snodo config get llm.coder.max_tool_turns
```

Anything else — and the keys an operator reaches for first are in this group,
`cloud.sync_enabled`, `cloud.liveness_interval_seconds`, `cloud.api_url`, and
the provider endpoint settings `base_url`, `litellm_provider` and
`api_key_env` — is answered with `Unknown config key` and can only be changed
by editing `~/.snodo/config.yml` by hand. The only provider field with its own
commands is `api_key`: `snodo config add` and `snodo config remove` manage it.

Anthropic, OpenAI, Google, OpenRouter, DeepSeek and Cloudflare Workers AI have
built-in provider configuration. `litellm_provider: openai` is what makes an
arbitrary compatible endpoint work: snodo rewrites `ollama/<model>` to
`openai/<model>` and sends it to `base_url`. Omit `api_key_env` for a local
server that needs no key. This covers Ollama Cloud, a local Ollama or
`llama.cpp` server, vLLM, LM Studio, and self-hosted gateways.

Read from the environment, never stored in the config file: `SNODO_HOME`,
`SNODO_TOKEN_SECRET`, `GITHUB_TOKEN` (for `--from-pr`), and any
`<PROVIDER>_API_KEY`.

## Check readiness before you spend

`snodo ready` is worth running first on an existing repository. It checks,
deterministically and without an LLM, whether every artefact the protocol
demands is **committed** — decision records, a resolvable test command, coder
configs, paths cited in criteria — and scores what is missing by how cheap it
is to fix. Task worktrees only see `HEAD`, so "present on disk" is not enough.

```
$ snodo ready
Method Scaffolding Readiness: 80% (4/5 checks satisfied)

Repository Readiness (Scored — travels with git repository):
  ❌ [warn] Path 'authentication/authorization' cited in criteria of validator
     'security' does not exist in repository.
     Fix: Create and commit 'authentication/authorization'
```

Repository findings are scored and travel with the repository; workstation
findings (missing binaries, plaintext keys) are reported but unscored. `--mode`
filters the displayed findings to one mode; `--json` emits the machine-readable
form.

## Propose criteria from your own decisions

A governed repository's records often state, in prose, the very rules the
protocol encodes by hand — that an id is derived server-side, that every query
carries a tenant filter. `snodo intake` reads those records and offers each
rule as a validator criterion, one at a time, each naming the record it came
from. The operator accepts or rejects each, and the protocol is written only
after an acceptance:

```
$ snodo intake
Intake: decision records under /path/to/repo
  1 criterion proposal(s), each citing the record it came from.
  • The org_id is derived server-side from the API key lookup, never accepted
    from the request.
      from: docs/decisions/001-derive-org-id-server-side.md

[1/1] The org_id is derived server-side from the API key lookup, never
      accepted from the request.
    from: docs/decisions/001-derive-org-id-server-side.md — Derive org_id server-side
Accept? [y/N]
```

Only a record's **Decision** section is proposable. Context is background,
Consequences are effects a reader can observe rather than rules to violate,
Alternatives considered names the road not taken, and Status is metadata —
none of them is offered. A record with no Decision section proposes nothing.
Use `--validator <id>` to choose the target validator (the default is the
protocol's `architecture` validator, then its first), `--reject-all` to see the
proposals and write nothing, or `--json` to have a machine read them without a
write. With no flag and no terminal, intake refuses rather than guessing.

## Quickstart

```bash
# 1. Initialize a project from a template
snodo init --template team

# 2. Run a task through the protocol
snodo run "implement a user registration endpoint" --mock

# 3. Run with a real LLM (requires configured API key)
snodo config add anthropic sk-ant-...
snodo run "add password reset flow"
```

The `--mock` flag uses a deterministic stub coder — no API call is made and no
key is spent. It still returns artifacts, so the protocol's gates, the worktree
isolation and the merge path are exercised end to end; useful for testing
protocol configuration and validator behaviour without a provider.

### Templates

The template list is derived from `snodo/protocols/templates/` — drop in a YAML file and it becomes selectable. Shipped templates:

| Template | Modes | When to use |
|----------|-------|-------------|
| `solo` | producer | Single developer, no review handoff |
| `team` | producer, reviewer, planner | Two-stage with separate approval authority |
| `2+n` | producer, reviewer | Paper reference config with scope/test/secrets predicates |
| `intent` | producer | Intent-driven, warn-only spec validators |
| `bugfix-surgeon` | producer | Bug-fix flow with a post-execute review gate |
| `feature-warden` | producer | Feature flow with a scope guard |
| `greenfield` | decide, scaffold, build | Phased build of a new project with per-phase exit gates |

Run `snodo init --template <name>` to pick one directly, or `snodo init` to choose from the menu.

### Coders

The coder writes; snodo governs, gates and records. `--coder` selects one:

| `--coder` | Mechanism | Needs | Auth |
|---|---|---|---|
| `litellm` *(default)* | In-process completions via LiteLLM | built-in | provider API keys |
| `opencode-cli` | Host `opencode run` | `opencode` on PATH | `opencode auth login` |
| `agy` | Antigravity CLI (`agy -p`) | `agy` on PATH | `agy login` |
| `opencode` *(experimental)* | OpenCode server in Docker over HTTP | Docker, `opencode:latest` | container env |
| `mock` | Deterministic stub | nothing | none |

Three things are worth knowing up front:

- **`-m` sets the *judging* model, not the coder's.** Validators and the
  classifier run on it. Host CLIs keep their own model catalogs; to pin a
  coder's model, namespace it — `--coder agy --model agy/gemini-2.5-pro`.
- **In-place coders own their commit.** `opencode`, `opencode-cli` and `agy`
  edit the worktree directly and commit, so post-execute validators judge the
  exact change. Any attempt to touch `.snodo/` halts as a blocker (ADR 027).
- **Selection order:** `--mock`, then `--coder`, then a mode's `coder:` field,
  then a model prefix (`agy/`, `opencode-cli/`, `claude`, `gpt`…), then
  `litellm`.

To add one, see the [coder adapter contract](architecture/coder-adapter-contract.md).

### Retrying a failed task

Retrying a failed task keeps its specification. `snodo run --retry <task_id>`
re-runs the task against the spec on record — that bare form is what the CLI
prints after a failure, so pasting it is safe. `--append-spec "…"` adds guidance
on top of that spec (a positional description does the same); `--replace-spec
"…"` replaces it, which is the only retry that discards anything, and the
discarded spec stays readable with `snodo task show <task_id>`.

### Completing a task outside the loop (by hand)

When an operator completes a task by hand outside the loop (for instance, inspecting a preserved worktree, merging the changes, and verifying the work manually), record the completion with:

`snodo task complete <task_id>`

This records the provenance in the audit log (who, when, unjudged by the engine) and updates the plan's `status.json` so the plan advances from that record rather than manual file edits. Supported options include `--plan`, `--who`, `--notes`, and `--json`.

An orchestrator connected over MCP records the same thing with the
`record_task_status` tool (`plan_name`, `task_id`, `status`, `who`, optional
`notes`). It is the machine-side `snodo task complete`: the same status
vocabulary, the same provenance and the same audit event, written through the
same implementation, so the plan state the two leave cannot diverge. A recorded
status is an operator's account — the audit entry is marked unjudged and
`outside_loop`, and it is never a validator verdict or a substitute for one.

## CLI Reference

`snodo <command> --help` is authoritative; the table below is the map.

| | |
|---|---|
| `init` | Scaffold `.snodo/` from a template |
| `run` | Execute a task, a plan (`--plan`), or a single wave (`--wave`). `--background`, `--resume`, `--retry`, `--from-pr`, `--interactive`, `--no-isolation` |
| `ready` | Score method-scaffolding readiness against the protocol |
| `intake` | Propose validator criteria from decision records; accept or reject each |
| `plan` | `list`, `status`, `create`, `validate`, `add-wave`, `add-task`, `run`, `delete` |
| `status` / `mode` | Active session and mode; `mode change` to switch |
| `session` | `list`, `show`, `new`, `switch`, `delete`, `prune` |
| `authorize` | Adjudicate escalated disagreements and `set_model` proposals |
| `validate` | Run a phase's validators without a coder and return the structured result |
| `audit verify` | Verify the hash chain |
| `job` / `logs` / `meta` | Background jobs: `list`, `status`, `logs`, `wait`, `cancel`; log streaming; usage |
| `task` / `worktree` | Task branches and the git worktrees used for isolation |
| `recon` | Fan out read-only agents to answer a question; `llm.recon.models` is an ordered failover list |
| `models` / `config` | Model discovery; keys and settings |
| `serve` | Run the protocol as an MCP server (stdio or SSE) |
| `cloud` | `connect`, `disconnect`, `status` for audit sync |
| `dashboard` | TUI (`snop`) |
| `agent` / `sandbox` / `install` / `uninstall` | Agent memory; Docker sandbox; Claude Desktop MCP entries |

### Models

`snodo models` lists the configured providers, and `snodo models --provider=<name>`
lists that provider's models with context window and price. `--stats` reports
what your own jobs actually spent and how they ran, aggregated from project
records.

`snodo models --benchmark` answers a different question: how fast is a model on
one fixed task? It sends **one prompt, the same prompt every run**, read from
`snodo/cli/commands/model_benchmark_prompt.txt` in the repository, and reports
the output tokens per second, the time to first token and the total wall time.
Because the prompt never varies, two providers produce two numbers worth
comparing — unlike `--stats`, whose rate moves with whatever prompts happened to
run. The prompt's identity (opening line, length and content hash) is printed
with the result, so you can tell whether two runs used the same prompt.
Changing the prompt file makes past numbers incomparable; the file says so.

Narrow the run to a single model with the same flags that filter a listing:

```bash
snodo models --benchmark --provider=deepseek --id-contains=chat
```

`--benchmark` makes one **real, billed API call**. It is never reachable from
any other command and runs only when you pass the flag; the command prints the
model, the prompt identity and the fact that it will spend before it does. The
output names the token-count basis as well: throughput is computed from the
provider's reported usage when it reports any, and from a local tokenizer when
it does not — a comparison between a provider-reported count and a locally
estimated one is flagged as such rather than silently averaged.

### Plan

| Command | Description |
|---------|-------------|
| `snodo plan create <intent>` | Create an empty plan to author into (never generates waves) |
| `snodo plan list` | List all plans |
| `snodo plan status <name>` | Show plan progress |
| `snodo plan validate <name>` | Verify plan structure and task spec files (`--json`) |
| `snodo run --plan <name>` | Execute a plan by name (`--wave N`, `--interactive`) |

Plans are authored, not generated: `plan create` scaffolds one empty wave, and
you add waves and tasks (ids are `<wave>.<seq>_<name>`, e.g. `1.1_models`) or
edit `plan.yml` directly. A plan is re-verified on every load. See
[runbooks/hand-authored-plan.md](runbooks/hand-authored-plan.md).

### Session

| Command | Description |
|---------|-------------|
| `snodo session list` | List sessions (filterable by `--mode`, `--project`) |
| `snodo session show <id>` | Show session details |
| `snodo session delete <id>` | Delete a session |
| `snodo session prune` | Remove stale sessions (>30 days by default) |

### Mode

| Command | Description |
|---------|-------------|
| `snodo mode show` | Show active mode |
| `snodo mode change <mode_id>` | Switch active mode |

### Config

| Command | Description |
|---------|-------------|
| `snodo config show` | Show configured keys (masked) |
| `snodo config add <provider> <key>` | Store an API key |
| `snodo config remove <provider>` | Remove an API key |
| `snodo config test` | Validate all configured keys |
| `snodo config set <section> <key> <value>` | Set a config value |
| `snodo config get <section> <key>` | Get a config value |

### Agent memory

| Command | Description |
|---------|-------------|
| `snodo agent list` | List all agents |
| `snodo agent memory <name>:<mode>` | Show agent memory summary |
| `snodo agent reset <name>:<mode>` | Clear memory, assign new thread |
| `snodo agent rotate <name>:<mode>` | Rotate thread ID (keeps checkpoints) |

### Authorization

| Command | Description | Key flags |
|---------|-------------|-----------|
| `snodo authorize [TASK_ID]` | Review and sign pending decisions | `--yes`, `--reject-all` |

### Jobs

| Command | Description |
|---------|-------------|
| `snodo job list` | List background jobs |
| `snodo job status <id>` | Show job status |
| `snodo job logs <id>` | Show job logs |
| `snodo job wait <id>` | Wait for completion |
| `snodo job cancel <id>` | Cancel a running job |

### Docker sandbox

| Command | Description |
|---------|-------------|
| `snodo sandbox build` | Build the worker image |
| `snodo sandbox status` | Check Docker availability |

Run with `snodo run ... --sandbox docker` to execute inside a container.

### Install / Uninstall (Claude Desktop)

| Command | Description |
|---------|-------------|
| `snodo install` | Install MCP servers into Claude Desktop config |
| `snodo uninstall` | Remove MCP servers from Claude Desktop config |

### Dashboard

```
snop
```

Or `snodo dashboard` — launches the Textual TUI for live session monitoring.

## MCP Serving

`snodo serve` starts one or two FastMCP servers based on the protocol:

```bash
# All modes — one server exposing all tools
snodo serve

# Single mode — only that mode's tool set
snodo serve --mode producer
snodo serve --mode reviewer
```

Server naming: `snodo-{protocol_id}` (all modes) or `snodo-{protocol_id}-{mode_id}` (single mode).

Connecting an orchestrator (Claude Desktop, custom agent):

```json
{
  "mcpServers": {
    "snodo-producer": {
      "command": "snodo",
      "args": ["serve", "--mode", "producer"]
    }
  }
}
```

Or use `snodo install` / `snodo uninstall` to manage the Claude Desktop config automatically.

### How modes become servers

Each protocol mode declares a set of logical tools (e.g., `edit`, `approve`, `pr`). `snodo serve` maps those to concrete MCP operations and serves exactly the mode's grant: tools the active mode(s) do not grant are not exposed at all (write_file and delete_file belong to no shipped grant), and no exposed call demands a validation token from the caller (ADR 047). The validator quorum is enforced inside the engine loop, per task.

### Validation flow (engine loop + INV3)

1. An orchestrator calls `validate_task` → the server runs the real pre-execute validators (the same quorum the loop runs)
2. If the quorum passes (policy threshold met, no blockers), a single-use JWT is recorded; the next `dispatch_task` consumes it as the audit link between the pre-check and the work
3. The orchestrator dispatches (`dispatch_task`, `run_plan`); the run validates the task again inside the engine loop before the coder executes
4. If any validator emits blocker, the task halts (INV3) — no token is issued and the loop does not proceed
5. If the task escalates (threshold not met, no blockers), use `snodo authorize` to review and sign

## Troubleshooting

### "Protocol file not found"

Run `snodo init` first, or specify the protocol path with `--protocol <path>`.

### Task blocked with "BLOCKED: ..."

The validators found issues. Check the structured halt payload for per-validator justifications:

```
--- STRUCTURED HALT PAYLOAD ---
{
  "halt_type": "escalated",
  "validator_results": [...],
  "hint": "Address the blocking concerns and re-run..."
}
```

Two refusal modes appear in the payload:

- **`halt_type: escalated`** — no single validator blocked, but the policy threshold wasn't met (e.g., unanimous needs all to pass, but some emitted warn). Use `snodo authorize <task_id>` to review and sign.
- **`halt_type: blocked`** — at least one validator emitted blocker (INV3). Address the blocking concern and re-run; blocking concerns cannot be voted down.

### Token expired or invalid

Tokens expire at the configured TTL (default 10 minutes) and are single-use. At the tool surface this is never a refusal: dispatch is not gated on a caller-held token (ADR 047). Inside the engine loop an expired token does stop the run — the session checkpoint preserves state, and the engine re-issues a new token on resume after re-validating.

### Quality validator runs subprocess tests

The `quality` validator type executes the repo's test suite via subprocess. It auto-detects the test command from common marker files (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Makefile`). Override in the protocol:

```yaml
validators:
  - validator_id: "quality"
    validator_type: "quality"
    evaluation_phase: "post_execute"
    tooling:
      test_command: "pytest"
      timeout: 120
```

### Session disappeared or cannot resume

Sessions are scoped to (mode, project). Use `snodo session list --mode <m>` to filter. Auto-resume finds the matching active session; if none exists, a new session is created.
