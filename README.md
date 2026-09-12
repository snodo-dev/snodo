# snodo
[![CI](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml/badge.svg)](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.scorecard.dev%2Fprojects%2Fgithub.com%2Fsnodo-dev%2Fsnodo&query=%24.score&label=openssf%20scorecard&suffix=%2F10&color=brightgreen)](https://scorecard.dev/viewer/?uri=github.com/snodo-dev/snodo)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/14420/badge)](https://www.bestpractices.dev/projects/14420)
[![PyPI](https://img.shields.io/pypi/v/snodo)](https://pypi.org/project/snodo/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-snodo.dev-2DD4BF)](https://docs.snodo.dev)
[![arXiv](https://img.shields.io/badge/arXiv-2606.20615-b31b1b)](https://arxiv.org/abs/2606.20615)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21967946.svg)](https://doi.org/10.5281/zenodo.21967946)
[![Coverage](https://codecov.io/gh/snodo-dev/snodo/branch/main/graph/badge.svg)](https://codecov.io/gh/snodo-dev/snodo)
[![Security Policy](https://img.shields.io/badge/security-policy-brightgreen)](SECURITY.md)

**AI-SDLC Protocol Engine** — a governance layer for AI-assisted software development.

![Three coder mechanisms — an in-process LLM client, a host CLI subprocess, and an opencode server in a Docker container — write into the same task worktree and converge on one identical gate and merge path.](https://raw.githubusercontent.com/snodo-dev/snodo/main/docs/assets/coder-paths.svg)

You define a *protocol* — a YAML specification of operational modes, validators
and constraints — and snodo executes tasks through it. Every task passes
validation gates before and after execution; a disagreement policy decides
whether the work proceeds, escalates to a human, or halts. What lands is what
your standards admitted, and the hash-chained audit log says why.

The coder is interchangeable and separate from the judge. An in-process LLM
client, a host CLI running on your own subscription, or a containerised server
all converge on the same gate and the same merge path.

**Preprint:** [*Specifying AI-SDLC Processes: A Protocol Language for Human-Agent Boundaries*](https://arxiv.org/abs/2606.20615) — arXiv:2606.20615.

## Project status

Actively-developed research implementation (beta).

| | |
|---|---|
| Code | ~39,400 lines across 5 packages (`snodo-core`, `snodo-tools`, `snodo-foundation`, `snodo-engine`, `snodo-mcp`) |
| Complexity | average cyclomatic complexity **A (4.9)** |
| Lint / architecture | `ruff` clean; package layering enforced in CI by `import-linter` |
| Python | 3.12 and 3.13 (CI matrix) |

The enforcement invariants — token integrity, capability boundaries,
non-overridable blockers, audit completeness — are verified by property-based
tests over randomized inputs.

## Install

```bash
pip install snodo
```

From source (a [`uv`](https://docs.astral.sh/uv/) workspace):

```bash
git clone https://github.com/snodo-dev/snodo.git
cd snodo
uv sync --all-extras
```

You need Python 3.12+, a model for the **validators** (always routed through
LiteLLM), and a **coder**, which need not be the same thing. Anthropic, OpenAI,
Google, OpenRouter, DeepSeek and Cloudflare Workers AI have built-in provider
configuration; any other OpenAI-compatible endpoint works by declaring a
provider block with `base_url` and `litellm_provider: openai` — Ollama, vLLM,
LM Studio, `llama.cpp`, self-hosted gateways. A local endpoint that needs no
key is not asked for one. `--coder opencode-cli` and `--coder agy` delegate
code generation to a CLI authenticated against your own subscription, so no
provider key is spent writing code. `--mock` needs nothing at all.

## Quickstart

```bash
snodo init --template solo                              # writes .snodo/protocol.yml
snodo config add anthropic sk-ant-...                   # store a provider key
snodo config set model claude-sonnet-4                  # default model
snodo ready                                             # is this project set up for the protocol?
snodo run "implement a hello world function"
```

A key already exported in your environment (`ANTHROPIC_API_KEY` and friends) is
auto-detected. To try the loop without spending anything, add `--mock`.

`snodo ready` is worth running first on an existing repository: it checks,
deterministically and without an LLM, whether every artefact the protocol
demands is **committed** — decision records, a resolvable test command, coder
configs, paths cited in criteria — and scores what is missing by how cheap it
is to fix. Task worktrees only see `HEAD`, so "present on disk" is not enough.

## Protocol language

| Concept | Description |
|---|---|
| **Mode** | An operational stage with a declared set of tools and validators. Disjoint tool sets enforce separation of duties — a producer cannot merge, a reviewer cannot edit. |
| **Validator** | An evaluation applied to a task. Has a `validator_type` (security, architecture, quality, conventions, planning…), an `evaluation_phase` (`pre_execute` or `post_execute`), and criteria — LLM prompt strings, or tooling config for deterministic checks. |
| **Disagreement policy** | How validator results combine: `unanimous`, `majority`, `quorum` (default 2/3), or `any`. |
| **Severity** | A result is `pass`, `warn` or `blocker`. A blocker halts regardless of policy. |
| **Constraint** | A rule enforced over execution artifacts through a predicate framework — deterministic, not judged. |
| **Transition** | A declarative event-to-mode mapping documenting intended handoffs. |

Seven templates ship with snodo:

| Template | Modes | For |
|---|---|---|
| `solo` | producer | A single developer with full access |
| `team` | producer, reviewer, planner | Three-mode team workflow |
| `2+n` | producer, reviewer | Paper reference configuration |
| `greenfield` | plan, decide, scaffold, build | A new project, from decisions to first code |
| `intent` | producer | Intent-driven authoring |
| `feature-warden` | producer | Feature development with a strict review gate |
| `bugfix-surgeon` | producer | Narrow, surgical defect work |

## Coders

The coder writes; snodo governs, gates, and records. Which one you pick does
not change what is enforced.

| `--coder` | Mechanism | Needs | Auth |
|---|---|---|---|
| `litellm` *(default)* | In-process completions via LiteLLM | built-in | provider API keys |
| `opencode-cli` | Host `opencode run` | `opencode` on PATH | `opencode auth login` |
| `agy` | Antigravity CLI (`agy -p`) | `agy` on PATH | `agy login` |
| `opencode` | OpenCode server in Docker over HTTP | Docker, `opencode:latest` | container env |
| `mock` | Deterministic stub | nothing | none |

Three things about them are worth knowing up front:

- **`-m` sets the *judging* model, not the coder's.** Validators and the
  classifier run on it. Host CLIs keep their own model catalogs; to pin a
  coder's model, namespace it — `--coder agy --model agy/gemini-2.5-pro`.
- **In-place coders own their commit.** `opencode`, `opencode-cli` and `agy`
  edit the worktree directly and commit, so post-execute validators judge the
  exact change. Any attempt to touch `.snodo/` halts as a blocker (ADR 027).
- **Selection order:** `--mock`, then `--coder`, then a mode's `coder:` field,
  then a model prefix (`agy/`, `opencode-cli/`, `claude`, `gpt`…), then
  `litellm`.

To add one, subclass `SubprocessCoderAdapter`, set `binary`, `model_prefix` and
`install_hint`, implement `_build_argv`, and register it in `CODER_REGISTRY`
(`snodo/coders/__init__.py`) — that alone exposes it to `--coder`, enables
prefix routing, and enrolls it in the adapter conformance suite.

## Commands

`snodo <command> --help` is authoritative; full reference at
[docs.snodo.dev](https://docs.snodo.dev).

| | |
|---|---|
| `init` | Scaffold `.snodo/` from a template |
| `run` | Execute a task, a plan (`--plan`), or a single wave (`--wave`). `--background`, `--resume`, `--retry`, `--from-pr`, `--interactive`, `--no-isolation` |
| `ready` | Score method-scaffolding readiness against the protocol |
| `plan` | `list`, `status`, `create`, `validate`, `add-wave`, `add-task`, `run`, `delete` |
| `status` / `mode` | Active session and mode; `mode change` to switch |
| `session` | `list`, `show`, `new`, `switch`, `delete`, `prune` |
| `authorize` | Adjudicate escalated disagreements and `set_model` proposals |
| `validate` | Check the protocol against the well-formedness rules |
| `audit verify` | Verify the hash chain |
| `job` / `logs` / `meta` | Background jobs: `list`, `status`, `logs`, `wait`, `cancel`; log streaming; usage |
| `task` / `worktree` | Task branches and the git worktrees used for isolation |
| `recon` | Fan out read-only agents to answer a question about the codebase |
| `models` / `config` | Model discovery; keys and settings |
| `serve` | Run the protocol as an MCP server (stdio or SSE) |
| `cloud` | `connect`, `disconnect`, `status` for audit sync |
| `dashboard` | TUI (`snop`) |
| `agent` / `sandbox` / `install` / `uninstall` | Agent memory; Docker sandbox; Claude Desktop MCP entries |

Plans are authored, not generated: `plan create` scaffolds one empty wave, and
you add waves and tasks (ids are `<wave>.<seq>_<name>`, e.g. `1.1_models`) or
edit `plan.yml` directly. A plan is re-verified on every load. See
[docs/runbooks/hand-authored-plan.md](docs/runbooks/hand-authored-plan.md).

Retrying a failed task keeps its specification. `snodo run --retry <task_id>`
re-runs the task against the spec on record — that bare form is what the CLI
prints after a failure, so pasting it is safe. `--append-spec "…"` adds guidance
on top of that spec (a positional description does the same); `--replace-spec
"…"` replaces it, which is the only retry that discards anything, and the
discarded spec stays readable with `snodo task show <task_id>`.

## Architecture

- **Mode-based capability separation.** Each mode declares its tools. WF1
  well-formedness forbids an approval-conferring tool from appearing in two
  modes, so separation of duties is structural rather than advisory.
- **Validator gates with disagreement policies.** `pre_execute` validators run
  before the coder, `post_execute` after. Results combine under the declared
  policy; a blocker halts immediately and cannot be overridden.
- **JWT validation tokens.** Agreement issues a signed token, and mutating MCP
  tools require a valid one — validation is non-overridable at the capability
  boundary, not just in the engine.
- **Hash-chained audit log.** Append-only, tamper-evident, and the record of
  every governance decision and verification. Optionally synced to snodo cloud.
- **Session resumability.** State is checkpointed under `$SNODO_HOME/sessions/`.
  Resume with `snodo run --resume <session_id>`; escalations are adjudicated
  with `snodo authorize` and the session continues.
- **Coder adapter pattern.** The backend sits behind a `CoderAdapter`
  interface, so a new one plugs in without touching the engine.
- **LangGraph execution engine.** The protocol compiles to a `StateGraph` built
  dynamically from the YAML — any arrangement of modes and validators.
- **Modular packages.** `snodo-core` (kernel: config, predicates, sandbox) →
  `snodo-tools` (workspace, git, shell, code hosts) → `snodo-foundation`
  (infrastructure, compiler, protocols) → `snodo-engine` (engine, validators,
  coders) → `snodo-mcp` (MCP servers, recon, jobs), with the root `snodo`
  package as CLI and dashboard. Layering is enforced in CI by `import-linter`.

## Configuration

Configuration lives in `~/.snodo/config.yml` (`$SNODO_HOME` overrides the
location). Manage it with `snodo config` rather than editing by hand:

```bash
snodo config add anthropic sk-ant-...
snodo config set model deepseek/deepseek-v4
snodo config show
```

```yaml
model: deepseek/deepseek-v4                   # default for all roles

llm:
  coder:
    max_tokens: 64000
    temperature: 0.1
  validator:
    model: openai/@cf/google/gemma-4          # role-specific override
    max_tokens: 25000
  recon:
    num_agents: 2

engine:
  max_subtask_depth: 3
  max_session_age_days: 30
  token_ttl_seconds: 1200

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
  sync_enabled: true
```

`litellm_provider: openai` is what makes an arbitrary compatible endpoint work:
snodo rewrites `ollama/<model>` to `openai/<model>` and sends it to `base_url`.
Omit `api_key_env` for a local server that needs no key.

Read from the environment, never stored in the config file: `SNODO_HOME`,
`SNODO_TOKEN_SECRET` (HMAC secret for token signing; random per process by
default), `GITHUB_TOKEN` (for `--from-pr`), and any `<PROVIDER>_API_KEY`.

## Research

> Prifti, Y. (2026). *Specifying AI-SDLC Processes: A Protocol Language for
> Human-Agent Boundaries.* arXiv:2606.20615.
> <https://doi.org/10.48550/arXiv.2606.20615>

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

Empirical studies live in `studies/`:

```bash
uv sync --extra studies
make studies
```

Architecture decisions are recorded in
[docs/decisions/](docs/decisions/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Copyright (C) 2026 The snodo Authors. Licensed under the Apache License,
Version 2.0 — see [LICENSE](LICENSE).
