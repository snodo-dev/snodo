# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.7.x | ✅ |
| < 0.7 | ❌ |

snodo is pre-1.0 and ships frequent patch releases. Only the latest
minor series receives security fixes; upgrade to the current release
rather than expecting a backport.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report security issues by emailing **security@snodo.dev**. Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Any suggested fix if you have one

You will receive an acknowledgement within 72 hours. We aim to release a fix
within 14 days of confirmed vulnerabilities.

## Scope

snodo is a local protocol engine. The primary security surface is:

- JWT validation token signing (`SNODO_TOKEN_SECRET`)
- MCP server tool gating (WF1 enforcement)
- API key storage via `snodo config`
- Protocol-file trust boundary: protocol-authored shell commands such as
  `tooling.test_command` and `prepare_command`
- Cloud sync egress: when `cloud.sync_enabled` is set, audit events are
  transmitted to the configured endpoint. What leaves the machine, in what
  shape, and what is never sent is documented in
  [docs/specs/cloud-sync.md](docs/specs/cloud-sync.md). Sync is opt-in and off
  by default; a run with it disabled makes no network call.

If you find a bypass of the WF1 token gate, a way to extract stored API keys,
or a path by which content named as never-transmitted reaches the network,
please report it.

## Threat model

snodo operates under a **trusted-repository model** (see
[ADR 014](docs/decisions/014-trusted-repository-threat-model.md)):

- snodo runs AI agents that execute code — including your test and build
  commands — inside the repository it is initialised in.
- The user explicitly runs `snodo init` in that repository; that act is the
  consent boundary. `snodo init` now warns about this and requires explicit
  confirmation (default **No**).
- Repository contents (`package.json` scripts, `conftest.py`, Makefiles, test
  code) are treated as trusted — equivalent to you running them yourself.
- A `protocol.yml` file is **executable input**, not passive configuration. The
  protocol fields `tooling.test_command` and `prepare_command` (for example
  `execution.prepare_command`) are executed through `shell=True`, and snodo does
  **not** sandbox protocol-authored commands. A protocol file must be trusted
  like a `Makefile`, `package.json` script, or CI config. Running a protocol you
  did not write is running code you did not read.
- Running snodo against untrusted or third-party code is out of scope and
  unsupported; isolation is not claimed. A hosted/multi-tenant deployment would
  invalidate this model and require real isolation.

The *agent* is treated as semi-untrusted: prompt injection can steer its tool
calls, so tool-input validation (argument injection, path traversal) remains
in scope even though the repository is trusted.
