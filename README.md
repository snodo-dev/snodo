# snodo

**AI-SDLC Protocol Engine** — a governance layer for AI-assisted software development.

snodo lets you define a *protocol* — a YAML specification of operational modes, validators, and constraints — and then executes tasks through that protocol. Each task passes through validation gates before and after execution, with disagreement policies (unanimous, majority, quorum, any) determining whether work proceeds, escalates, or halts. The result is a structured, auditable workflow where AI-generated code is checked against your standards before it lands.

Preprint: coming soon on arXiv

## Install

### From PyPI (recommended)

```bash
pip install snodo
```

### From source

```bash
git clone https://github.com/snodo-dev/snodo.git
cd snodo
pip install -e ".[dev]"
```

### Requirements

- Python 3.12+
- API keys for your LLM provider (Anthropic, OpenAI, or Google)

## Quickstart

### 1. Initialize a project

```bash
snodo init --template solo
```

This creates `.snodo/protocol.yml` with a single-mode protocol:

```yaml
protocol_id: "solo"
name: "Solo Developer Protocol"
version: "1.0.0"

modes:
  - mode_id: "producer"
    name: "Producer Mode"
    tools:
      - "edit"
      - "dispatch"
      - "resolve"
      - "test"
      - "validate"
      - "commit"
      - "merge"
    validators:
      - "security"
      - "architecture"
      - "quality"
    transitions: {}

validators:
  - validator_id: "security"
    validator_type: "security"
    evaluation_phase: "pre_execute"
    criteria:
      - "Check for security vulnerabilities"
      - "Validate input sanitization"
      - "Check authentication/authorization"

  - validator_id: "architecture"
    validator_type: "architecture"
    evaluation_phase: "pre_execute"
    criteria:
      - "Check design patterns"
      - "Validate separation of concerns"
      - "Check for tight coupling"

  - validator_id: "quality"
    validator_type: "quality"
    evaluation_phase: "post_execute"
    tooling: {}  # auto-detect from repo marker files

disagreement_policy: "unanimous"
initial_mode: "producer"

global_constraints: []
```

### 2. Configure your API key

```bash
snodo config add anthropic sk-ant-...
```

Or set the environment variable directly (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`).

### 3. Run a task

```bash
snodo run "implement a hello world function"
```

### 4. Dry run with mock coder (no API key needed)

```bash
snodo run "implement a hello world function" --mock
```

## Protocol Language

A protocol is defined in YAML and consists of:

| Concept | Description |
|---|---|
| **Mode** | An operational stage with a defined set of tools and validators. Modes enforce separation of capabilities (e.g., producer can edit, reviewer can merge). |
| **Validator** | An evaluation criterion applied to a task. Each has a `validator_type` (security, architecture, quality, conventions, protocol, planning, etc.), an `evaluation_phase` (`pre_execute` or `post_execute`), and criteria (LLM prompt strings or tooling config). |
| **Disagreement Policy** | How validator results are combined: `unanimous` (all must pass), `majority` (>50%), `quorum` (configurable threshold, default 2/3), or `any` (at least one). |
| **Severity** | Validator results are `pass`, `warn`, or `blocker`. Any blocker halts execution regardless of policy. |
| **Constraint** | A rule enforced over execution artifacts (e.g., files must be within scope, tests must exist for modified code). Constraints use a predicate framework for deterministic evaluation. |
| **Transition** | A declarative event-to-mode mapping that documents the protocol's intended mode handoffs. |

Three templates ship with snodo:

| Template | Modes | Description |
|---|---|---|
| `solo` | producer | Single developer with full access |
| `team` | producer, reviewer, planner | Three-mode team workflow |
| `2+n` | producer, reviewer | Paper reference config: producer + reviewer with N validators and global constraints |

## CLI Reference

### `snodo init`

Initialize a snodo project.

```
--template, -t TEXT   Protocol template: solo, team, or 2+n
--force, -f           Overwrite existing .snodo/ directory
--mode, -m TEXT       Starting mode (skips interactive picker)
```

### `snodo run`

Execute a task through the protocol.

```
DESCRIPTION             Task description (required unless --plan is used)
--protocol TEXT         Path to protocol file [default: .snodo/protocol.yml]
--model, -m TEXT        Model to use (e.g., claude-sonnet-4-20250514, gpt-4)
--verbose               Show detailed output
--mock                  Use mock coder instead of real LLM
--plan, -p TEXT         Execute a plan by name
--wave, -w INTEGER      Execute only a specific wave (requires --plan)
--interactive, -i       Confirm each task before execution
--from-pr INTEGER       Fetch PR comments as task context
--background, -b        Run task in background
--sandbox TEXT          Sandbox type: local or docker [default: local]
--resume TEXT           Resume execution from session ID
```

### `snodo serve`

Start MCP server from protocol definition.

```
--protocol TEXT         Path to protocol file [default: .snodo/protocol.yml]
--mode TEXT             Serve a single mode (default: all modes)
--transport TEXT        Transport type: stdio or sse [default: stdio]
--port INTEGER          Port for SSE transport [default: 8080]
--install               Install MCP servers into Claude Desktop config (deprecated)
--uninstall             Remove this project's MCP entries (deprecated)
--uninstall-all         Remove ALL snodo MCP entries (deprecated)
--project-name TEXT     Override project name for MCP entry naming
```

### `snodo plan`

Manage plans. Subcommands: `list`, `status`, `create`.

```
snodo plan create DESCRIPTION    Create a new plan from an intent description
  --name, -n TEXT                Plan name (auto-generated if omitted)
  --protocol TEXT                Path to protocol file
  --model, -m TEXT               Model to use
  --mock                         Use mock coder instead of real LLM
```

### `snodo session`

Manage protocol sessions. Subcommands: `list`, `show`, `delete`, `prune`.

```
snodo session list
  --mode TEXT       Filter by mode
  --project TEXT    Filter by project path
  --status TEXT     Filter by status
```

### `snodo mode`

Manage active protocol mode. Subcommands: `show`, `change`.

```
snodo mode change NEW_MODE
```

### `snodo config`

Manage API keys and configuration. Subcommands: `show`, `add`, `remove`, `test`, `set`, `get`.

```
snodo config add PROVIDER KEY    Store an API key (provider: openai, anthropic, google)
```

### `snodo resolve`

Resolve an escalated validator disagreement.

```
SESSION_ID TASK_ID               Session and task to resolve
--decision, -d TEXT              Resolution: proceed or halt [required]
--justification, -j TEXT         Justification for the decision [required]
--resolved-by TEXT               Who resolved (default: cli)
```

### `snodo job`

Manage background jobs. Subcommands: `list`, `status`, `logs`, `wait`, `cancel`.

```
snodo job logs JOB_ID
  --stream, -s TEXT    Log stream: stdout or stderr [default: stdout]
  --tail, -n INTEGER   Show last N lines
```

### `snodo agent`

Manage agent memory and threads. Subcommands: `list`, `memory`, `reset`, `rotate`.

### `snodo sandbox`

Manage Docker sandbox. Subcommands: `build`, `status`.

```
snodo sandbox build
  --tag, -t TEXT    Image tag (default: snodo-worker:latest)
```

### `snodo install`

Install MCP servers into Claude Desktop config.

```
--protocol TEXT    Path to protocol file [default: .snodo/protocol.yml]
```

### `snodo uninstall`

Remove MCP servers from Claude Desktop config.

```
--mode TEXT        Remove a single mode entry
--all              Remove ALL snodo-* entries from Claude config
--purge            Also delete .snodo/ directory and sessions
--orphans          Detect and remove orphan MCP entries
--yes, -y          Skip confirmation prompts
```

### `snodo dashboard`

Launch the TUI dashboard (`snop`).

## Architecture

- **Mode-based capability separation.** Each mode declares its available tools. Disjoint tool sets between modes (enforced by WF1 well-formedness checks) ensure structural separation of duties — a producer cannot merge, a reviewer cannot edit.

- **Validator gates with disagreement policies.** Tasks pass through `pre_execute` validators before execution and `post_execute` validators after. Results (pass/warn/blocker) are combined via a configurable policy (unanimous, majority, quorum, any). Any blocker halts immediately.

- **JWT validation tokens.** When validators agree, a signed JWT token is issued. Mutating MCP tools require a valid token (WF1 enforcement at the server level), making validation non-overridable at the capability boundary.

- **Session resumability.** Execution state is checkpointed to `~/.snodo/sessions/` (or `$SNODO_HOME/sessions/`). Sessions can be resumed with `snodo run --resume <session_id>`. Escalated disagreements are resolved via `snodo resolve` and the session continues.

- **Coder adapter pattern.** The code generation backend is abstracted behind a `CoderAdapter` interface. Built-in adapters include `LiteLLMAdapter` (any LiteLLM-supported model) and `MockAdapter` (deterministic stubs for testing). New backends can be plugged in without changing the engine.

- **LangGraph execution engine.** The protocol is compiled into a LangGraph `StateGraph` with nodes for governance, validation, execution, and completion. The graph is dynamically built from the protocol YAML, supporting arbitrary mode and validator configurations.

## Environment Variables

| Variable | Purpose |
|---|---|
| `SNODO_HOME` | Override the snodo home directory (default: `~/.snodo`). Config, sessions, and agent memory all live under this path. |
| `SNODO_TOKEN_SECRET` | Override the HMAC secret for JWT validation token signing (default: randomly generated per process). |
| `ANTHROPIC_API_KEY` | Anthropic API key (auto-detected for `claude-*` models). |
| `OPENAI_API_KEY` | OpenAI API key (auto-detected for `gpt-*` and `o1-*`/`o3-*` models). |
| `GEMINI_API_KEY` | Google API key (auto-detected for `gemini-*` models). |
| `GITHUB_TOKEN` | GitHub token for PR-related features (`--from-pr`). |

## Research

snodo is described in a research paper covering the protocol language, well-formedness conditions, and empirical evaluation of disagreement policies.

> Preprint: coming soon on arXiv

Empirical studies are included in the `studies/` directory and can be run with:

```bash
pip install -e ".[studies]"
make studies
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

Copyright (C) 2026 The snodo Authors

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).
See the [LICENSE](LICENSE) file for details.
