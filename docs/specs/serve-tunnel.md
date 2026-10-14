# snodo serve --tunnel: managed remote tunnel via snodo.dev

## Intent
One command gives engineers a stable public HTTPS URL protected by OAuth
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
      Response: {hostname, tunnel_token}
  6. Store in .snodo/tunnel.json:
        {hostname, tunnel_token, created_at}
  7. Start MCP server (subprocess, same as snodo serve)
  8. Start cloudflared: cloudflared tunnel run --token {tunnel_token}
  9. Wait for cloudflared to connect (parse stdout for "Registered
     tunnel connection")
  10. Print:

  ✓ Snodo MCP tunnel active

  Connect your MCP client through OAuth:

    URL:    https://\{hostname\}/mcp
    OAuth auth server: https://mcp-auth.snodo.dev

  Press Ctrl+C to stop.

Subsequent runs (tunnel.json exists):
  1. Start MCP server
  2. Start cloudflared with stored tunnel_token
  3. Print:

  ✓ Snodo MCP tunnel active: https://\{hostname\}/mcp
   OAuth auth server: https://mcp-auth.snodo.dev

  Press Ctrl+C to stop.

Ctrl+C handling:
  Stop both subprocesses (MCP server + cloudflared) cleanly

## snodo-cloud API (spec for snodo-cloud, implement separately)

POST {cloud.tunnel_api_url}/tunnel/provision   # default https://app.snodo.dev
  Auth: Bearer {snodo_api_key}
  Body: {project_slug, mode, short_id, snodo_version}
  Creates: Cloudflare tunnel + DNS record
  Returns: {hostname, tunnel_token}


## .snodo/tunnel.json schema
{
  "hostname": "acme-api-prod-a3f7.tunnel.snodo.dev",
  "tunnel_token": "eyJ...",
  "created_at": "2026-06-07T..."
}

## Acceptance criteria
- First run: provisions tunnel and starts services behind OAuth
- Subsequent runs: uses stored config, starts services
- Ctrl+C: both subprocesses stop cleanly
- cloudflared not installed: clear error with install instructions
- No snodo account: clear message with signup URL
- stdio transport unchanged, no --tunnel behaviour bleeds into default

## Testing
- Unit: tunnel.json stores only tunnel hostname, token, and metadata
- Unit: cloudflared not found → clear error
- Unit: no snodo account → clear message
- Unit: Ctrl+C → both processes stop
- Integration: end-to-end with real cloudflared (e2e marked test)
- Full suite passes

## Constraints
- Read cli/commands/serve_cmd.py, cli/main.py,
  infrastructure/config.py (how API key is stored/read) before
  touching anything
- OAuth bearer tokens are verified by the tunnel's MCP server
- cloudflared is a subprocess — not a Python dependency
- snodo-cloud provisioning API is a separate ticket (snodo-cloud repo)
- Touch: serve_cmd.py, cli/main.py only in snodo-public
