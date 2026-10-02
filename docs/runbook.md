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

To keep config-based keys without leaving them in plaintext, run
`snodo config --encrypt-provider-keys`. It backs up `config.yml` as
`config.yml.bak` (or a numbered backup if one exists), creates a dedicated
provider-encryption RSA keypair in `~/.ssh/NO-AGENT/` on first use, and moves
each plaintext provider key to `~/.ssh/NO-AGENT/keys/<provider>.key` (directory
0700, files 0600). The config then contains a quoted reference such as
`api_key: "@keys/openai.key"`. Existing references are skipped, so rerunning
the command is safe. Snodo decrypts a key only when that provider is used.
Keep the dedicated `provider-keys.pem` private key to read these files; it is
separate from the audit-signing `snodo.pem` and survives `snodo init --force-keygen`.
This is **obfuscation, not a vault**: anyone with the private key and encrypted
files can decrypt the provider credentials. The config backup still contains
the original plaintext keys; remove it securely once you no longer need it.

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

### User configuration reference

The complete schema, defaults, environment references, cloud settings, and all
five notification target formats live in the canonical
[user configuration reference](configuration.md). Notification targets belong
in `~/.snodo/config.yml` (or `$SNODO_HOME/config.yml`), not in a project
protocol. Test them with `snodo config --notify-test`.

Example `~/.snodo/config.yml` with common role settings and one webhook:

```yaml
model: claude-sonnet-4-20250514

llm:
  num_retries: 3
  coder:
    model: null
    max_tokens: 16000
    max_tool_turns: 6
    timeout_seconds: 1800
    silence_timeout_seconds: 600
    concurrency: 1
  validator:
    model: null
    max_tokens: 1500
    max_tool_turns: 6
  classifier:
    model: null
    max_tokens: 500
    temperature: 0.0
  recon:
    num_agents: 1
    models: []
  wave:
    max_age_days: 14
    max_idle_days: 5

engine:
  max_subtask_depth: 3
  max_session_age_days: 30
  token_ttl_seconds: 600

cloud:
  api_key: "" # add a literal Snodo Cloud key to enable sync
  api_url: https://api.snodo.dev
  tunnel_api_url: https://app.snodo.dev
  sync_enabled: false
  liveness_interval_seconds: 60

notifications:
  targets:
    - type: webhook
      name: team-updates
      url: env:SNODO_WEBHOOK_URL
  events:
    - job_finished
    - task_halted
    - authorization_needed
    - job_silent
  silence_threshold_seconds: 900
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

Anything else — including cloud settings, notification targets, and provider
endpoint settings such as `base_url`, `litellm_provider`, and `api_key_env` —
is answered with `Unknown config key` and can only be changed by editing
`~/.snodo/config.yml` by hand. The provider `api_key` field has dedicated
commands: `snodo config add` and `snodo config remove`.

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
# In a new, empty project directory; an existing repository with a commit can
# skip the Git initialization and initial commit steps.
printf '# My project\n' > README.md
git init
git add README.md
# Git must have user.name and user.email configured for this commit.
git commit -m "Initial project commit"

pip install snodo
pip install pytest
snodo init --template team --test-command "pytest"
snodo run "add a hello() function that returns the string 'world', with a test" --mock
```

This is the canonical first-run command sequence; the [README](../README.md) and
[docs home](index.md) link here. Git needs a committer identity before the
commit: configure it with `git config --global user.name "Your Name"` and
`git config --global user.email "you@example.com"`, or set repository-local
values with `git config user.name "Your Name"` and
`git config user.email "you@example.com"`.

The starter README ensures the initial commit has a file, and Snodo's isolated
task worktrees start from that committed `HEAD`. In an existing repository,
skip `git init`; if it has no commits, commit at least one project file first.
The mock coder deterministically writes `src/hello.py` and
`tests/test_hello.py`, implementing the requested hello-world task without a
provider API call. With the `pytest` dependency and test command in the sequence,
the quality validator runs pytest and the generated test passes. A mock result
does not establish that a real coder can implement your requirements.

For provider-backed coding, configure a provider (for example,
`snodo config add anthropic <key>` or `ANTHROPIC_API_KEY`) and run the same task
description without `--mock`. Task isolation is required by default;
`--no-isolation` is an explicit fallback that runs in the current working tree.

For an MCP run, the orchestrator validates and dispatches a task (or builds a
plan/queue when the work needs that structure). Work starters return a job ID;
follow it using the returned browser watch link over the configured HTTP/tunnel
endpoint or `snodo logs <job_id> --watch`. `watch_job`'s MCP Apps panel is an
optional host feature; clients without Apps support receive a text snapshot.

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
| `greenfield` | plan, decide, scaffold, build | Phased build of a new project with per-phase exit gates |

Run `snodo init --template <name>` to pick one directly, or `snodo init` to choose from the menu.

### Coders

The coder writes; snodo governs, gates and records. `--coder` selects one:

| `--coder` | Mechanism | Needs | Auth |
|---|---|---|---|
| `litellm` *(default)* | In-process completions via LiteLLM | built-in | provider API keys |
| `opencode-cli` | Host `opencode run` | `opencode` on PATH | `opencode auth login` |
| `agy` | Antigravity CLI (`agy -p`) | `agy` on PATH | `agy login` |
| `codex-cli` | OpenAI Codex CLI (`codex exec`) | `codex` on PATH | `codex login` |
| `opencode` *(experimental)* | OpenCode server in Docker over HTTP | Docker, `opencode:latest` | container env |
| `mock` | Deterministic stub | nothing | none |

Three things are worth knowing up front:

- **`-m` sets the *judging* model, not the coder's.** Validators and the
  classifier run on it. Host CLIs keep their own model catalogs; to pin a
  coder's model, namespace it — `--coder codex-cli --model codex-cli/gpt-5-codex`.
- **In-place coders own their commit.** `opencode`, `opencode-cli`, `codex-cli` and `agy`
  edit the worktree directly and commit, so post-execute validators judge the
  exact change. Any attempt to touch `.snodo/` halts as a blocker (ADR 027).
- **Selection order:** `--mock`, then `--coder`, then a mode's `coder:` field,
  then a model prefix (`codex-cli/`, `agy/`, `opencode-cli/`, `claude`, `gpt`…), then
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

## CLI reference

The maintained, complete command and flag index is the
[command reference](command-reference.md). Model discovery and selection
guidance is in [Choosing models](choosing-models.md); plan authoring is in
[Authoring a plan](authoring-a-plan.md).

## MCP Serving

`snodo serve` starts one FastMCP server for the resolved protocol mode. Without
`--mode`, it resolves the current mode; it does not combine grants from all
modes. Pin a mode explicitly when you want that mode's tool set:

```bash
# Current mode
snodo serve

# Single mode — only that mode's tool set
snodo serve --mode producer
snodo serve --mode reviewer
```

Server naming: `snodo-{protocol_id}` for the current project mode, or
`snodo-{protocol_id}-{mode_id}` for a server pinned with `--mode`.

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

Use `snodo serve --mcp-install` to write MCP server entries to detected Claude
Desktop JSON configuration and the shared Codex / ChatGPT desktop TOML
configuration (`~/.codex/config.toml`, or `$CODEX_HOME/config.toml`). Codex
entries include startup and tool timeouts, and the launcher points to the Snodo
installation environment that ran the installer. The installer skips clients
whose config directory is absent. Restart updated clients after installation
for the new configuration to take effect. `--mcp-uninstall`,
`--mcp-uninstall-all`, `--mcp-list`, and orphan cleanup cover those clients too.

### How modes become servers

Each protocol mode declares logical tools; `snodo serve` maps each grant to
concrete MCP operations. A tool a mode does not grant is not exposed. Current
templates grant the `write` capability where their workflow needs plan/config
authoring; it exposes `write_file` only, and `delete_file` is not exposed.
Unknown capability grants are warned about when an MCP server is created and
reported by `snodo ready`, with the mode, unknown grant, and known capability
names; they do not expose tools.

| Protocol mode tool | MCP tool(s) | Purpose |
|---|---|---|
| `edit` | `read_file`, `list_files`, model/recon tools | Read and understand project files |
| `write` | `write_file` | Write files under the protocol's allowed prefixes |
| `approve` / `commit` | `stage_files`, `commit` | Stage and commit changes |
| `pr` | `create_change_request`, `read_change_request_diff`, `post_change_request_comment`, `approve_change_request`, `request_change_request_changes`, `merge_change_request`, `read_change_request_discussion` | Create and manage vendor-neutral change requests; legacy `*_pr` tools remain available as deprecated aliases |

`write_file` writes directly; it does not stage or commit. Its default path
allowance is `.snodo/`. A protocol can replace that default with a top-level
prefix list, for example:

```yaml
write_allowed_prefixes:
  - .snodo/
  - docs/specs/
```

Paths are resolved against the project root (including `..` and symlinks),
must remain inside an allowed prefix, and cannot name a directory. Refusals
name the allowed prefixes. Successful writes are recorded in the audit log
with their path, byte count, SHA-256 content hash, mode, and session. Grant
`write` only to modes that need this capability. No exposed call demands a
validation token from the caller (ADR 047); the validator quorum is enforced
inside the engine loop, per task.

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

The validators or runtime returned an outcome. Check the structured halt
payload for canonical `halt_type`, raw cause, and per-validator justifications:

```
--- STRUCTURED HALT PAYLOAD ---
{
  "halt_type": "escalate",
  "validator_results": [...],
  "hint": "Address the blocking concerns and re-run..."
}
```

For example:

- **`halt_type: escalate`** — the policy threshold was not met and there was no blocker. A human may review and sign with `snodo authorize <task_id>`.
- **`halt_type: blocker`** — at least one validator returned blocker (INV3). Address the blocking concern; it cannot be authorized away.
- **`validator_error`, `internal_error`, or `environment_error`** — an operational failure, not a verdict about the task. Diagnose the error and do not feed it back as task-spec critique.

### Token expired or invalid

Tokens expire at the configured TTL (default 10 minutes) and are single-use. At the tool surface this is never a refusal: dispatch is not gated on a caller-held token (ADR 047). Inside the engine loop an expired token does stop the run — the session checkpoint preserves state, and the engine re-issues a new token on resume after re-validating.

### Quality validator runs subprocess tests

The `quality` validator type executes the repo's test suite via subprocess. It
uses a declared `tooling.test_command` or detects one from project marker files.
Shipped templates provide a no-op fallback if no test command is configured or
detected; the audit outcome is `no_tests`, not a claim that tests passed.
Override in the protocol:

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
