# snodo
[![CI](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml/badge.svg)](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/snodo)](https://pypi.org/project/snodo/)
[![Python](https://img.shields.io/pypi/pyversions/snodo)](https://pypi.org/project/snodo/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-snodo.dev-2DD4BF)](https://docs.snodo.dev)
[![arXiv](https://img.shields.io/badge/arXiv-2606.20615-b31b1b)](https://arxiv.org/abs/2606.20615)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-2125%20passing-3DBF4F)](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/snodo-dev/snodo/main/.github/badges/coverage.svg)](.github/badges/coverage.svg)

**AI-SDLC Protocol Engine** — a governance layer for AI-assisted software development.

snodo lets you define a *protocol* — a YAML specification of operational modes, validators, and constraints — and then executes tasks through that protocol. Each task passes through validation gates before and after execution, with disagreement policies (unanimous, majority, quorum, any) determining whether work proceeds, escalates, or halts. The result is a structured, auditable workflow where AI-generated code is checked against your standards before it lands.

**Preprint:** [*Specifying AI-SDLC Processes: A Protocol Language for Human-Agent Boundaries*](https://arxiv.org/abs/2606.20615) — arXiv:2606.20615 ([doi:10.48550/arXiv.2606.20615](https://doi.org/10.48550/arXiv.2606.20615)).

## Project status

snodo is an actively-developed research implementation (beta). Current state of the codebase:

| Metric | Value |
|---|---|
| Code | ~33,800 lines across **5 packages** (`snodo-core`, `snodo-tools`, `snodo-foundation`, `snodo-engine`, `snodo-mcp`) |
| Complexity | average cyclomatic complexity **A (3.9)** — no high-complexity hotspots |
| Lint / architecture | `ruff` clean; package layering enforced in CI by `import-linter` |
| Python | 3.12 and 3.13 (CI matrix) |

The enforcement invariants (token integrity, capability boundaries, non-overridable blockers, audit completeness) are verified by property-based tests over randomized inputs.

## Install

### From PyPI (recommended)

```bash
pip install snodo
```

### From source

snodo is a [`uv`](https://docs.astral.sh/uv/) workspace of modular packages:

```bash
git clone https://github.com/snodo-dev/snodo.git
cd snodo
uv sync --all-extras   # installs all workspace packages editable, plus dev + studies extras
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
      - "meta-spec"
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

Configuration lives in `~/.snodo/config.yml`, managed via the `snodo config` commands:

```bash
snodo config add anthropic sk-ant-...      # store a provider key
snodo config set model claude-sonnet-4     # set the default model
```

A provider key already exported in your environment (e.g. `ANTHROPIC_API_KEY`) is auto-detected if it isn't in the config. See [Configuration](#configuration) for the full file format.

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

## Coder Backends

snodo executes protocol tasks under interchangeable code generation backends (**coders**). The coder generates code, while snodo enforces protocol governance, runs validation gates, and maintains an append-only audit log.

### Supported and External Coders

| Coder (`--coder`) | Description | Type | Requirements | Authentication |
|---|---|---|---|---|
| `litellm` *(default)* | Direct LLM completions via LiteLLM (~100+ providers) | Engine-Managed | Python `litellm` (built-in) | Provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. or `snodo config add`) |
| `opencode` | OpenCode server running in Docker container over HTTP | In-Place Container | Docker daemon running; image `opencode:latest` | OpenCode config/env variables inside container |
| `opencode-cli` | Host `opencode run` CLI invocation | In-Place Host CLI | `opencode` CLI on PATH | `opencode auth login` or host provider env vars (`OPENROUTER_API_KEY`, etc.) |
| `agy` | Antigravity CLI (`agy -p`) host invocation | In-Place Host CLI | `agy` CLI on PATH | `agy login` / Google Cloud host credentials |
| `mock` | Deterministic stub for dry-runs and testing | Stub | None | None |

### Model Role Separation: Judging vs Execution

- **`-m` / `--model` sets the JUDGING model**: The model passed via `-m` (e.g. `-m claude-3-5-sonnet` or `-m deepseek/deepseek-v4`) is resolved through LiteLLM for **validators** (pre-execute and post-execute gates) and the **classifier** (intent routing).
- **External CLI coders use their own model catalogs**: Host CLI tools like `agy` or `opencode-cli` maintain their own internal model catalogs and CLI settings. Passing a judging model identifier to an external CLI coder is meaningless; `SubprocessCoderAdapter._bare_model()` automatically strips non-prefixed model names so the CLI falls back to its own default/last-selected model.
- **Explicit Coder Model Override**: To specify a coder's model explicitly while keeping `-m` for validators, prefix the model string with the coder's namespace:
  ```bash
  snodo run "implement feature" --coder agy --model agy/gemini-2.5-pro
  snodo run "implement feature" --coder opencode-cli --model opencode-cli/claude-3-7-sonnet
  ```

### Coder Selection Precedence

`resolve_coder_name()` selects the active coder by evaluating criteria in strict order:

1. **Explicit Mock Flag**: `--mock` / `use_mock_coder=True` (always returns `'mock'`).
2. **Explicit CLI Flag**: `--coder <name>` (e.g., `snodo run "task" --coder agy`).
3. **Protocol Mode Field**: `coder: <name>` declared in a mode definition in `.snodo/protocol.yml` (`modes[].coder`).
4. **Model Prefix Mapping**: Inferred from model prefix: `opencode-cli/` → `opencode-cli`, `opencode/` → `opencode`, `agy/` → `agy`, `gpt`/`o1`/`o3` → `openai`, `claude` → `anthropic`, `gemini`/`google/` → `gemini`.
5. **Default Fallback**: `'litellm'`.

### In-Place Coders vs `litellm`

External coders (`opencode`, `opencode-cli`, `agy`) inherit `InPlaceCoderAdapter` (`skip_engine_commit = True`, `skip_workspace_write = True`):

- **In-Place File Writes**: External coders edit files directly in the workspace working tree.
- **Commit Ownership**: The adapter stages and commits changes to git upon completion (`InPlaceCoderAdapter._commit_changes()`), advancing `HEAD` so post-execute validators reviewing `git diff HEAD~1..HEAD` see the exact change produced.
- **`.snodo/` Mutation Guard**: Any attempt by an in-place coder to modify the `.snodo/` directory triggers a `SnodoMutationError` and halts execution as a `snodo_mutation_blocked` blocker (ADR 027).
- **No Per-Turn Usage or Token Records**: Per **ADR 034**, the absence of turn-by-turn usage and token metrics for external coders is a **stated decision (non-goal)**, not an attestation gap. Token and cost data reside in per-job operational telemetry (`state.json` via `snodo meta`), whereas snodo's hash-chained audit trail attests to governance decisions and verification evidence across all coders.

### Adding a New Coder Adapter

New host CLI coder adapters inherit `SubprocessCoderAdapter` (`snodo.coders.subprocess_adapter`), which provides shared subprocess execution, prompt construction, git diff readback, and artifact construction.

Creating a new CLI adapter requires specifying four class attributes and implementing `_build_argv`:

```python
from typing import List
from snodo.coders.subprocess_adapter import SubprocessCoderAdapter

class CustomCoderAdapter(SubprocessCoderAdapter):
    binary: str = "custom-coder"
    model_prefix: str = "custom/"
    install_hint: str = "Install custom-coder: https://example.com/install"

    def _build_argv(self, prompt: str, project_root: str, model: str) -> List[str]:
        argv = [self.binary, "run", "--dir", project_root, prompt]
        if model:
            argv.extend(["--model", model])
        return argv
```

Registering the adapter class in `CODER_REGISTRY` (`snodo/coders/__init__.py`) automatically exposes it to `--coder`, enables model prefix routing, and includes it in the adapter conformance test suite (`tests/coders/test_adapter_conformance.py`).

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
--coder TEXT            Coder backend: litellm, opencode, opencode-cli, agy, mock
--model, -m TEXT        Model to use for judging (e.g., claude-sonnet-4-20250514, gpt-4)
--verbose               Show detailed output
--mock                  Use mock coder instead of real LLM
--plan, -p TEXT         Execute a plan by name
--wave, -w INTEGER      Execute only a specific wave (requires --plan)
--interactive, -i       Confirm each task before execution
--from-pr INTEGER       Fetch PR comments as task context
--background, -b        Run task in background
--sandbox TEXT          Sandbox type: local or docker [default: local]
--resume TEXT           Resume execution from session ID
--retry                 Retry execution of a halted task using saved failure context
--retain-worktree       Retain task git worktree after execution finishes
--no-isolation          Run task directly in the main repository working tree
```

### `snodo status`

Show status of the active snodo session, mode, and task progress.

### `snodo validate`

Validate the project protocol definition and well-formedness rules.

```
--protocol TEXT         Path to protocol file [default: .snodo/protocol.yml]
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

Manage plans. Subcommands: `list`, `status`, `create`, `validate`,
`add-wave`, `add-task`, `run`, `delete`.

```
snodo plan list                  List all plans with wave and task counts
snodo plan status NAME           Show per-wave task progress
snodo plan create DESCRIPTION    Create an empty plan to author into
  --name, -n TEXT                Plan name (auto-generated from the
                                 description if omitted)
  --protocol TEXT                Path to protocol file
                                 [default: .snodo/protocol.yml]
  --model, -m TEXT               Model to use (accepted; currently unused)
  --mock                         Use mock coder instead of real LLM
                                 (accepted; currently unused)
snodo plan validate NAME         Verify plan structure and task spec files
  --json                         Emit the result as JSON
                                 (schema snodo.plan_validate.v1)
```

`create` scaffolds `plan.yml` and `status.json` with a single empty wave, so a
new plan already validates — `validate` rejects a plan with no waves at all.
It does not generate waves from the description: `Tasks: 0` on a fresh plan is
expected. Author the plan with the commands below, by editing `plan.yml`
directly, or through the MCP planner's `generate_spec`.

```
snodo plan add-wave NAME ID      Add a wave (integer id) to the plan
  --depends-on TEXT              Comma-separated wave ids this wave depends on
snodo plan add-task NAME TASK_ID Add a task to a wave from a spec file
  --spec-file PATH               Markdown spec for the task (required)
  --parent TEXT                  Parent task ref, for subtask depth tracking
  --replace                      Overwrite an existing task spec
snodo plan delete NAME           Remove a plan directory
  --force                        Delete even when tasks are completed or
                                 in progress
```

Task ids are `<wave>.<seq>_<name>`, e.g. `1.1_models`. Wave 1 exists from
`create`, so the first wave you add is `2`. Both `add-wave` and `add-task`
re-verify the plan afterwards and report if the edit left it invalid.

Execute a plan with either form — they take the same arguments and run the
same code:

```
snodo plan run NAME              Execute the plan's tasks, wave by wave
snodo run --plan NAME            Equivalent
  --wave, -w INTEGER             Execute only a specific wave
  --interactive, -i              Confirm each task before execution
```

A plan is verified before its first task runs and every time it is loaded.
See [docs/runbooks/hand-authored-plan.md](docs/runbooks/hand-authored-plan.md)
for a worked example of authoring and running a plan by hand.

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

#### Custom OpenAI-Compatible Providers (e.g. Ollama Cloud)

You can bring your own OpenAI-compatible endpoint by declaring a custom provider block in `~/.snodo/config.yml`. Specify `litellm_provider: openai` to route completions via LiteLLM's OpenAI handler:

```yaml
providers:
  ollama:
    litellm_provider: openai
    base_url: https://ollama.com/v1
    api_key_env: OLLAMA_API_KEY
```

With this configuration:
- `snodo models --provider=ollama --flush` discovers models directly from `https://ollama.com/v1/models`.
- `snodo run "implement feature" --model=ollama/llama-3.3-70b-instruct` executes tasks using your custom provider endpoint without requiring an `openai` provider block.

### `snodo authorize`

Review and authorize (or reject) pending decisions the orchestrator escalated to a human — disagreement adjudications and `set_model` proposals.

```
snodo authorize [TASK_ID]
  --yes, -y       Skip the confirmation prompt
  --reject-all    Bulk-reject all pending decisions
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

### `snodo recon`

Fan out read-only exploration agents to answer a question about the codebase.

```
snodo recon "how does token issuance work?" [PATHS...]
  --agents, -n INTEGER   Number of agents to fan out (default: from config)
```

### `snodo models`

List available models for a provider, with cost and capability filters.

```
snodo models --provider anthropic --id-contains sonnet
```

### `snodo logs`

Stream logs for a job or recon run.

```
snodo logs <j_xxx | rec_xxx> [--watch]
```

### `snodo meta`

Show metadata and usage for a job or task.

```
snodo meta <j_xxx | task_xxx>
```

### `snodo cloud`

Manage the snodo cloud connection and audit sync. Subcommands: `connect`, `disconnect`, `status`.

```
snodo cloud connect <api-key>
```

### `snodo task`

Manage task branches. Subcommands: `list`, `abandon`, `prune`.

### `snodo worktree`

Manage git worktrees used for task isolation. Subcommands: `list`, `remove`, `prune`.

```
snodo worktree list
snodo worktree remove WORKTREE_NAME
snodo worktree prune
```

## Architecture

- **Mode-based capability separation.** Each mode declares its available tools. Disjoint tool sets between modes (enforced by WF1 well-formedness checks) ensure structural separation of duties — a producer cannot merge, a reviewer cannot edit.

- **Validator gates with disagreement policies.** Tasks pass through `pre_execute` validators before execution and `post_execute` validators after. Results (pass/warn/blocker) are combined via a configurable policy (unanimous, majority, quorum, any). Any blocker halts immediately.

- **JWT validation tokens.** When validators agree, a signed JWT token is issued. Mutating MCP tools require a valid token (WF1 enforcement at the server level), making validation non-overridable at the capability boundary.

- **Session resumability.** Execution state is checkpointed to `~/.snodo/sessions/` (or `$SNODO_HOME/sessions/`). Sessions can be resumed with `snodo run --resume <session_id>`. Escalated disagreements are adjudicated via `snodo authorize` and the session continues.

- **Coder adapter pattern.** The code generation backend is abstracted behind a `CoderAdapter` interface. Built-in adapters include `LiteLLMAdapter` (any LiteLLM-supported model), provider-specific adapters (Anthropic, OpenAI, Gemini), an `OpenCodeAdapter` (containerised OpenCode), and `MockAdapter` (deterministic stubs for testing). New backends can be plugged in without changing the engine.

- **LangGraph execution engine.** The protocol is compiled into a LangGraph `StateGraph` with nodes for governance, validation, execution, and completion. The graph is dynamically built from the protocol YAML, supporting arbitrary mode and validator configurations.

- **Modular package layout.** The codebase is split into independently-installable packages under a `uv` workspace: `snodo-core` (kernel — config, predicates, sandbox), `snodo-tools` (workspace/git/shell primitives and code-host providers), `snodo-foundation` (infrastructure, compiler, protocols), `snodo-engine` (execution engine, validators, coders), and `snodo-mcp` (MCP servers, recon, jobs) — with the root `snodo` package as the CLI and dashboard app. Dependency layering is enforced in CI by `import-linter`.

## Configuration

snodo stores its configuration in `~/.snodo/config.yml` (override the location with `$SNODO_HOME`). Manage it with the `snodo config` commands rather than editing by hand:

```bash
snodo config add anthropic sk-ant-...        # add a provider API key
snodo config set model deepseek/deepseek-v4  # default model for all roles
snodo config set engine.max_subtask_depth 3
snodo config show
```

A typical `config.yml`:

```yaml
model: deepseek/deepseek-v4                   # default model for all roles

llm:
  coder:                                      # per-role overrides
    max_tokens: 64000
    temperature: 0.1
  validator:
    model: openai/@cf/google/gemma-4          # role-specific model override
    max_tokens: 25000
  classifier:
    model: openai/@cf/google/gemma-4          # omit -> falls back to top-level `model`
  recon:
    num_agents: 2
    models:
      - deepseek/deepseek-v4

engine:
  max_subtask_depth: 3
  max_session_age_days: 30
  token_ttl_seconds: 1200

providers:
  anthropic:
    api_key: sk-ant-...
    api_key_env: ANTHROPIC_API_KEY            # env var the key is injected into at runtime
  deepseek:
    api_key: sk-...
    api_key_env: DEEPSEEK_API_KEY
  cloudflare:
    api_key: cfut_...
    api_key_env: OPENAI_API_KEY               # Cloudflare Workers AI via the OpenAI-compatible endpoint
    account_id: <account-id>
    base_url: https://api.cloudflare.com/client/v4/accounts/<account-id>/ai/v1

cloud:                                        # optional: snodo cloud sync
  api_url: https://api.snodo.dev
  sync_enabled: true
```

Each provider's `api_key` is injected into its `api_key_env` environment variable when a matching model runs, so provider SDKs pick it up automatically.

### Environment variables

These are read directly from the environment (not stored in `config.yml`):

| Variable | Purpose |
|---|---|
| `SNODO_HOME` | Override the snodo home directory (default: `~/.snodo`). Config, sessions, and agent memory live here. |
| `SNODO_TOKEN_SECRET` | Override the HMAC secret for JWT validation-token signing (default: randomly generated per process). |
| `GITHUB_TOKEN` | GitHub token for PR-related features (`--from-pr`). |
| `<PROVIDER>_API_KEY` | Any provider key set in the environment is auto-detected if it isn't already in `config.yml`. |

## Research

snodo is described in a research paper covering the protocol language, well-formedness conditions, enforcement invariants, and empirical evaluation of disagreement policies.

> Prifti, Y. (2026). *Specifying AI-SDLC Processes: A Protocol Language for Human-Agent Boundaries.* arXiv:2606.20615. <https://doi.org/10.48550/arXiv.2606.20615>

```bibtex
@misc{prifti2026snodo,
  title         = {Specifying AI-SDLC Processes: A Protocol Language for Human-Agent Boundaries},
  author        = {Prifti, Ylli},
  year          = {2026},
  eprint        = {2606.20615},
  archivePrefix = {arXiv},
  doi           = {10.48550/arXiv.2606.20615},
  url           = {https://arxiv.org/abs/2606.20615}
}
```

Empirical studies are included in the `studies/` directory and can be run with:

```bash
uv sync --extra studies
make studies
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

Copyright (C) 2026 The snodo Authors

Licensed under the Apache License, Version 2.0.
See the [LICENSE](LICENSE) file for details.
