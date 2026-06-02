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
snodo config set engine max_subtask_depth 5     # default: 3, range 1-10
snodo config set engine max_session_age_days 60  # default: 30, range 1-365
snodo config set engine token_ttl_seconds 1200   # default: 600, range 60-86400
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

The `--mock` flag uses a stub coder — no API call, no files created. Useful for testing protocol configuration and validator behaviour.

### Templates

| Template | Modes | When to use |
|----------|-------|-------------|
| `solo` | producer | Single developer, no review handoff |
| `team` | producer, reviewer, planner | Two-stage with separate tool sets |
| `2+n` | producer, reviewer | Paper reference config with scope/test/secrets predicates |

## CLI Reference

### Core commands

| Command | Description | Key flags |
|---------|-------------|-----------|
| `snodo init` | Create `.snodo/` with a protocol | `--template solo\|team\|2+n`, `--force` |
| `snodo run <desc>` | Execute a task through the protocol | `--mock`, `--model`, `--from-pr <N>`, `--background`, `--sandbox docker` |
| `snodo serve` | Start MCP server(s) | `--mode <id>` (single mode), `--port <N>` |

### Plan

| Command | Description |
|---------|-------------|
| `snodo plan create <intent>` | Decompose intent into structured plan |
| `snodo plan list` | List all plans |
| `snodo plan status <name>` | Show plan progress |

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

### Resolution

| Command | Description |
|---------|-------------|
| `snodo resolve <session_id> <task_id>` | Resolve escalated disagreement | `--decision proceed\|halt`, `--justification "..."` |

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

# Single mode — only that mode's disjoint tool set
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

Each protocol mode declares a set of logical tools (e.g., `edit`, `approve`, `pr`). `snodo serve` maps those to concrete MCP operations with per-tool WF1 enforcement: every mutating operation requires a valid JWT validation token. Read-only operations (read_file, list_files, get_status) require no token.

### Validation flow (WF1 + INV3)

1. An orchestrator calls `validate_task` → the engine runs the configured validators
2. If all pass (policy threshold met, no blockers), a JWT validation token is issued
3. The orchestrator calls mutating tools (write_file, commit, etc.) with the token
4. If any validator emits blocker, the task halts (INV3) — no token is issued, no mutations allowed
5. If the task escalates (threshold not met, no blockers), use `snodo resolve` to proceed or halt

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

- **`halt_type: escalated`** — no single validator blocked, but the policy threshold wasn't met (e.g., unanimous needs all to pass, but some emitted warn). Use `snodo resolve` to proceed or halt.
- **`halt_type: blocked`** — at least one validator emitted blocker (INV3). Address the blocking concern and re-run; blocking concerns cannot be voted down.

### Token expired or invalid

Tokens expire at the configured TTL (default 10 minutes). A task that sits idle between validation and execution may see `WF1 violation: token required`. Re-run the task — the session checkpoint preserves state, and the engine re-issues a new token on resume.

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
