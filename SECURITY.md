# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ |

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

If you find a bypass of the WF1 token gate or a way to extract stored API
keys, please report it.
