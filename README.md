# snodo
[![CI](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml/badge.svg)](https://github.com/snodo-dev/snodo/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.scorecard.dev%2Fprojects%2Fgithub.com%2Fsnodo-dev%2Fsnodo&query=%24.score&label=openssf%20scorecard&suffix=%2F10&color=brightgreen)](https://scorecard.dev/viewer/?uri=github.com/snodo-dev/snodo)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/14420/badge)](https://www.bestpractices.dev/projects/14420)
[![PyPI](https://img.shields.io/pypi/v/snodo)](https://pypi.org/project/snodo/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-snodo.dev-2DD4BF)](https://docs.snodo.dev)
[![arXiv](https://img.shields.io/badge/arXiv-2606.20615-b31b1b)](https://arxiv.org/abs/2606.20615)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.21967946-blue)](https://doi.org/10.5281/zenodo.21967946)
[![Coverage](https://img.shields.io/codecov/c/github/snodo-dev/snodo/main)](https://codecov.io/gh/snodo-dev/snodo)
[![Security Policy](https://img.shields.io/badge/security-policy-brightgreen)](SECURITY.md)
[![Built with snodo](https://img.shields.io/badge/built%20with-snodo-2DD4BF)](#built-with-snodo)

**Enforce your development process around whichever AI agent writes the code.**

![How snodo runs a task: an agent dispatches over MCP, pre-execute validators issue a signed token, any coder writes the change in its own worktree, post-execute validators run the tests, and clean work arrives as a pull request. A warning sends the findings back to the coder, which corrects and retries until the validators pass. A single blocker halts the run until a human acknowledges it and revises the intent, which starts the run again. Every step is appended to a hash-chained audit log.](https://raw.githubusercontent.com/snodo-dev/snodo/main/docs/assets/snodo-flow.svg)

## Built with snodo

From v0.19.0, snodo is developed with snodo. Each change to this repository
starts as a task spec, passes snodo's validators, is written by a coding agent
in its own worktree, is verified by the test gate, and arrives here as a pull
request that snodo opened. The protocol that governs this repository is
[`.snodo/protocol.yml`](.snodo/protocol.yml), and every run is recorded in its audit
log.

AI coding agents are fast, confident and non-deterministic. They will report a
task as finished while a test is failing, while the change has drifted outside
the task, or while it contradicts a decision your team recorded months ago.
Asking the agent again does not settle any of that. snodo is built for the gap:
you describe your development process as a *protocol*, and the engine enforces
it around whichever agent writes the code.

The protocol is a YAML file. It declares **modes** — the stages of your process,
each with an explicit tool set — **validators** that must agree before work
proceeds, and the **policy** for when they disagree. Enforcement is structural,
not advisory: mutations are gated behind a signed validation token, a single
`blocker` halts execution before any vote (so a critical finding cannot be
outvoted), and every governance decision is written to a hash-chained audit log.
snodo does not make an agent more reliable. It makes the process hold whether or
not the agent cooperates.

## The working loop

Connect an agent to Snodo over MCP. Group work into plans. A plan is one clear
intention. Waves group its tasks: tasks in a wave run in parallel, and waves run
in series. Queues group and schedule plans. Dispatch a single task only for a
true one-off with no related work. A plan with one task in one wave adds nothing.
The orchestrator then validates and starts the work. Each task still passes through the
protocol's validator loop; jobs run asynchronously. Follow task status and
progress in the configured cloud live view (`/now?task_ref=<task_ref>`) when
cloud sync is configured, and follow live output with
`snodo logs <job_id> --watch`. `watch_job` also has an optional MCP Apps panel,
but some hosts (including Claude Desktop and relayed sessions) show only its
plain-text snapshot.

Register Snodo's mode-pinned MCP servers with `snodo serve --mcp-install`.
Installation supports Claude Desktop, Claude Code, Cursor, Gemini CLI and the
Codex family (ChatGPT desktop, Codex CLI and IDE extension). It writes their
user-level configurations: Claude Desktop's platform-specific config,
Claude Code's `~/.claude.json`, Cursor's `~/.cursor/mcp.json`, Gemini CLI's
`~/.gemini/settings.json`, and Codex's `~/.codex/config.toml` (relocated by
`CODEX_HOME`). Only detected clients are updated; unrelated MCP entries are
preserved and each updated client is reported. Restart each updated client to
connect.

Optional job notifications can send project-named updates to ntfy, a generic
webhook, Slack, Discord, or Teams. Configure targets in
`~/.snodo/config.yml` (`$SNODO_HOME/config.yml` when overridden), then check
them with `snodo config --notify-test`. The [configuration reference](docs/configuration.md)
covers all user-level settings.

```text
orchestrator → MCP validate/dispatch or plan/queue → job → live browser watch
                                                     └→ snodo logs <job_id> --watch
```

That makes snodo an **AI-SDLC protocol engine**: a governance layer over the
software development lifecycle, for teams where AI agents are first-class
contributors. The coder is interchangeable and separate from the judge — an
in-process LLM client, a host CLI authenticated against your own subscription,
or a containerised server all converge on the same gate and the same merge path.

## Install and first run

Follow the canonical [first-run quickstart](docs/runbook.md#quickstart). It
includes the starter file needed for the initial commit, the Git identity
prerequisite, and the same governed hello-world mock task shown in the docs.
Try the published package without installing it with `uvx snodo --version`, or
install it for regular use with `uv tool install snodo`; `pip install snodo` is
also available as a fallback. Python 3.12+ is required.

### Container image

Build the Snodo engine image from the repository root with
`docker build -f docker/Dockerfile -t snodo .`. Run it against a mounted Git
repository with `docker run --rm -v "$PWD:/workspace" -w /workspace snodo ready`;
the image's entrypoint is `snodo`, so CLI arguments are passed directly. The
image contains no code-host credentials; provide any required credentials through
your runtime's normal environment or secret mechanism.

Before spending anything on an existing repository, run `snodo ready`. Without
an LLM, it checks whether the artefacts the protocol expects — decision records,
a resolvable test command, coder configs — are **committed**, and scores what is
missing by how cheap it is to fix. Task worktrees only see `HEAD`, so "present
on disk" is not enough. See the [runbook](docs/runbook.md) for readiness, the
full configuration surface, and the command reference.

## What a mock run does

The canonical quickstart's `--mock` run uses a deterministic stub coder that
writes `src/hello.py` and `tests/test_hello.py`; it implements the hello-world
task. The task still passes through the configured governance and verification
flow; with pytest installed and configured, the generated test passes. The
mock makes no provider API call and does not prove that a real coder can
implement your project's requirements. For provider-backed coding, configure
credentials (for example, `snodo config add anthropic <key>` or
`ANTHROPIC_API_KEY`) and run without `--mock`.

## Project status

Actively-developed research implementation (beta). The enforcement invariants —
token integrity, capability boundaries, non-overridable blockers, audit
completeness — are verified by property-based tests over randomised inputs.
Measured size, complexity and the CI gates are on the [docs home](docs/index.md).

Three honest boundaries, so you meet them here rather than an hour in:

- **The `opencode` coder paths are experimental.** `litellm` (the default),
  `agy` and `mock` are supported. The built-in `litellm` coder exists so snodo
  works with nothing else installed; if an expert CLI is available, prefer it.
- **The repository is trusted, not sandboxed.** snodo runs your test and build
  commands, and a protocol file is executable input, like a `Makefile` or a CI
  config. [ADR 014](docs/decisions/014-trusted-repository-threat-model.md) is the
  threat model; do not point it at untrusted code.
- **This is a preprint-stage research artifact.** The paper is under review;
  treat the code as a working implementation of the ideas, not a finished
  product.

## Where to go next

| Page | What it covers |
|---|---|
| [Protocol reference](docs/protocol.md) | The full `protocol.yml` language: modes, validators, constraints, disagreement policies, well-formedness, templates |
| [Coder backends](docs/coders.md) | Every `--coder`, how selection works, and how adapters are added |
| [Runbook](docs/runbook.md) | Install, configure, the CLI reference, MCP serving, troubleshooting |
| [Configuration](docs/configuration.md) | User settings, provider credentials, cloud, and job notifications |
| [Architecture](docs/architecture.md) | How enforcement works end to end, the package map, the invariant-to-mechanism table |
| [Machine interface](docs/machine-interface.md) | The versioned `--json` contract and validation-outcome exit codes |
| [Authoring a plan](docs/authoring-a-plan.md) | The contract for hand- and orchestrator-authored multi-wave plans |
| [Design decisions](docs/decisions/README.md) | Every ADR, from PyJWT over custom HMAC to module-scoped governance |
| [Docs home](https://docs.snodo.dev) | The published site |

## Research

**Preprint:** [*Specifying AI-SDLC Processes: A Protocol Language for Human-Agent Boundaries*](https://arxiv.org/abs/2606.20615) — arXiv:2606.20615.

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

Empirical studies live in [`studies/`](studies/), and the paper's claims are
mapped to code and tests in [Research](docs/research/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Copyright (C) 2026 The snodo Authors. Licensed under the Apache License,
Version 2.0 — see [LICENSE](LICENSE).
