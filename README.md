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

**Enforce your development process around whichever AI agent writes the code.**

![Three coder mechanisms — an in-process LLM client, a host CLI subprocess, and an opencode server in a Docker container — write into the same task worktree and converge on one identical gate and merge path.](https://raw.githubusercontent.com/snodo-dev/snodo/main/docs/assets/coder-paths.svg)

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

That makes snodo an **AI-SDLC protocol engine**: a governance layer over the
software development lifecycle, for teams where AI agents are first-class
contributors. The coder is interchangeable and separate from the judge — an
in-process LLM client, a host CLI authenticated against your own subscription,
or a containerised server all converge on the same gate and the same merge path.

## Install and first run

```bash
pip install snodo
snodo init --template solo
snodo config add anthropic sk-ant-...
snodo run "add a hello() function that returns the string 'world', with a test"
```

Python 3.12+. A key already exported in your environment (`ANTHROPIC_API_KEY`
and friends) is auto-detected. `snodo run ... --mock` needs no key and no
network at all.

Make an initial commit before the first run: each task executes in a git
worktree on a branch off `HEAD`, and on a repository with no commits snodo
refuses to run rather than silently dropping isolation (ADR 025).

Before spending anything on an existing repository, run `snodo ready`. Without
an LLM, it checks whether the artefacts the protocol expects — decision records,
a resolvable test command, coder configs — are **committed**, and scores what is
missing by how cheap it is to fix. Task worktrees only see `HEAD`, so "present
on disk" is not enough. See the [runbook](docs/runbook.md) for readiness, the
full configuration surface, and the command reference.

## What a run looks like

A project initialised from the `solo` template with its test command declared
(`--test-command "python -m pytest -q"`), then:

```bash
$ snodo run "add a hello() function that returns the string 'world', with a test" --mock

✓ Loaded protocol: Solo Developer Protocol
  Validators: security, architecture, quality, meta-spec, acceptance
  Policy: unanimous

  Validating (pre-execute): security, architecture, meta-spec
    meta-spec: finished
    architecture: finished
    security: finished
  Coder dispatched
  Coder returned (3 artifact(s))
  Post-validating: quality, acceptance
    acceptance: finished
    quality: finished
✓ Verified merge for task/task_ca8fd940fe5d/add-a-hello-function-that: task
  task_ca8fd940fe5d verified at commit 3e144ca (python -m pytest -q).

task_ca8fd940fe5d  resolved  (depth=0)
```

`--mock` swaps in a deterministic stub coder, so no key is spent and no network
call is made. The gates are not stubbed: the post-execute
`quality` validator ran this project's declared test command
(`python -m pytest -q`) against the change and passed it. Replace `--mock` with
a configured provider or `--coder opencode-cli` and the same gates judge the
real change. The transcript is trimmed for length; the protocol language is in
the [protocol reference](docs/protocol.md) and the coder backends are in
[Coder backends](docs/coders.md).

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
