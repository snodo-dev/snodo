# snodo serve --tunnel: managed remote tunnel via snodo.dev

## Intent
One command gives engineers a stable public HTTPS URL and service token
for their snodo MCP server. No Cloudflare account, no DNS config, no
domain required. Requires a free snodo account (snodo-cloud provisions
the tunnel infrastructure). The core engine works without this — this
is a convenience feature for remote AI client connectivity.

## User flow

First run:
  snodo serve --tunnel [--transport streamable-http] [--port 8000]

  1. Check cloudflared is installed (which cloudflared)
     If absent: print install instructions, exit with clear error
  2. Check .snodo/tunnel.json — if exists, skip to "Subsequent runs"
  3. Check snodo account: read ~/.snodo/config.yml for api_key
     If absent: prompt to sign up / log in:
       "snodo serve --tunnel requires a free snodo account.
        Sign up at: https://app.snodo.dev
        Then run: snodo auth login"
     Exit if no account.
  4. Generate short_id: 6 random alphanumeric chars
   5. POST {cloud.tunnel_api_url}/tunnel/provision
      (tunnel worker base, default https://app.snodo.dev — NOT
       cloud.api_url, which is the audit ingest base):
       {project_slug, mode, short_id, snodo_version}
     Auth: snodo API key in Authorization header
     Response: {hostname, tunnel_token, client_id, client_secret}
  6. Store in .snodo/tunnel.json:
       {hostname, tunnel_token, client_id, created_at}
     NOTE: client_secret is NOT stored — shown once only
  7. Start MCP server (subprocess, same as snodo serve)
  8. Start cloudflared: cloudflared tunnel run --token {tunnel_token}
  9. Wait for cloudflared to connect (parse stdout for "Registered
     tunnel connection")
   10. Only after the local MCP listener is bound AND cloudflared reports
       "Registered tunnel connection", print:

  ✓ Snodo MCP tunnel active

  Configure in your AI provider (Claude, Gemini, ChatGPT, etc.):

    URL:    https://\{hostname\}/mcp
    Header: CF-Access-Client-Id: {client_id}
    Header: CF-Access-Client-Secret: {client_secret}

  ⚠  Save the Client Secret — it will not be shown again.
     Rotate with: snodo serve --tunnel --rotate

  Press Ctrl+C to stop.

Subsequent runs (tunnel.json exists):
  1. Start MCP server
  2. Start cloudflared with stored tunnel_token
   3. Wait for "Registered tunnel connection" before printing active. If
      registration is not confirmed within 30 seconds, stop both child process
      groups and return failure; do not print or imply that a URL is active.
      Show redacted cloudflared diagnostics and advise checking network access
      and retrying. A newly provisioned tunnel is rolled back; if rollback fails,
      retain its hostname/token and give the deliberate --delete recovery command.
      An existing tunnel is never automatically deleted on connection failure.
   4. Print:

  ✓ Snodo MCP tunnel active: https://\{hostname\}/mcp
  (Use your saved CF-Access-Client-Id and CF-Access-Client-Secret)

   Press Ctrl+C to stop.

“Tunnel active” proves that the local MCP listener bound and cloudflared
registered a connection with Cloudflare. It does not prove end-to-end client
reachability, hostname/DNS propagation, or that a client's authentication is
configured correctly.

Failed deletion recovery:
  A failed Cloud DELETE (including HTTP 500) does not establish that the
  tunnel or DNS has been removed. The CLI retains the local hostname/token
  and records local recovery metadata, but refuses to start the saved tunnel
  or claim it is active. Retry deliberately with:
    snodo serve --tunnel --delete --hostname <hostname>
  A successful HTTP 200 or no-record HTTP 404 clears the local record only
  when it names that same target. An explicit different hostname never clears
  or changes the unrelated local record. Cloud owns retry-safe resource cleanup;
  this CLI does not infer resource state from error text or blindly erase local
  credentials. Tunnel connections are OAuth-only; legacy saved auth settings
  do not disable OAuth, and --auth service-token is rejected before side effects.

--rotate flag:
  Select one rotatable service credential. If none exists, explain that OAuth
  bearer credentials expire and cannot be rotated. If several exist, require
  --credential <name> rather than guessing.
  DELETE api.snodo.dev/tunnel/{hostname}/token (revokes old token)
  POST api.snodo.dev/tunnel/{hostname}/token (creates new token)
  Update tunnel.json with new client_id
  Print new client_id + client_secret (once)

Ctrl+C handling:
  Stop both subprocesses (MCP server + cloudflared) cleanly

## snodo-cloud API (spec for snodo-cloud, implement separately)

POST {cloud.tunnel_api_url}/tunnel/provision   # default https://app.snodo.dev
  Auth: Bearer {snodo_api_key}
  Body: {project_slug, mode, short_id, snodo_version}
  Creates: Cloudflare tunnel + DNS record + Access app + service token
  Returns: {hostname, tunnel_token, client_id, client_secret}

DELETE api.snodo.dev/tunnel/{hostname}/token
POST api.snodo.dev/tunnel/{hostname}/token
  Token rotation

## .snodo/tunnel.json schema
{
  "hostname": "acme-api-prod-a3f7.tunnel.snodo.dev",
  "tunnel_token": "eyJ...",
  "client_id": "88bf3b6d....access",
  "created_at": "2026-06-07T..."
}
client_secret never stored.

## Acceptance criteria
- First run: provisions tunnel, shows client_secret once, starts services
- Subsequent runs: uses stored config, starts services
- --rotate: revokes old token, issues new one, shows new secret once
- Ctrl+C: both subprocesses stop cleanly
- cloudflared not installed: clear error with install instructions
- No snodo account: clear message with signup URL
- stdio transport unchanged, no --tunnel behaviour bleeds into default

## Testing
- Unit: tunnel.json written correctly (no client_secret)
- Unit: cloudflared not found → clear error
- Unit: no snodo account → clear message
- Unit: Ctrl+C → both processes stop
- Unit: --rotate calls correct API endpoints
- Integration: end-to-end with real cloudflared (e2e marked test)
- Full suite passes

## Constraints
- Read cli/commands/serve_cmd.py, cli/main.py,
  infrastructure/config.py (how API key is stored/read) before
  touching anything
- client_secret NEVER written to disk — shown once, user's
  responsibility
- cloudflared is a subprocess — not a Python dependency
- snodo-cloud provisioning API is a separate ticket (snodo-cloud repo)
- Touch: serve_cmd.py, cli/main.py only in snodo-public
