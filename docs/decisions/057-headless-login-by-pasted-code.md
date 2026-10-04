# ADR 057 — Headless cloud login by pasting an OAuth code

## Status

Accepted (2026-10-04).

## Context

`snodo cloud login --no-browser` previously printed an authorization URL whose
localhost callback existed only on the headless machine. A browser on another
machine could not reach it, forcing operators to relay the callback manually.

## Decision

Use the cloud-hosted `https://mcp-auth.snodo.dev/cli/code` page as the redirect
for `--no-browser`. It displays one copyable value in `code#state` form. The CLI
accepts that complete value (and bare codes for compatibility), validates state
when supplied, and exchanges the code using the cloud redirect URI. PKCE remains
enabled; its verifier never leaves the machine. Register public clients with
both `http://localhost:*` and the cloud code redirect. When a legacy cached
registration lacks that redirect, create a replacement for the headless flow;
the replacement becomes active only after a successful token exchange, keeping
an existing signed-in session intact if login fails.

Choose paste-code sign-in rather than device authorization because snodo-cloud
is deploying the browser page and contract, enabling a short copy/paste flow
without introducing a second authorization protocol or polling lifecycle.

## Consequences

Headless machines can complete OAuth through any browser while keeping the PKCE
verifier local. Normal browser login continues to use its loopback callback.
Registrations created by current clients support both methods.

## Alternatives

Device authorization was considered but not selected; the agreed cloud page
provides the required cross-device handoff with the existing authorization-code
and PKCE flow.
