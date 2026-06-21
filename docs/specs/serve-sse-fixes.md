# Fix snodo serve SSE transport + remote access hint

## Intent
Three fixes to make SSE transport actually work, plus a hint for
engineers who want to expose their local MCP server remotely using
their own tooling.

## What to change

### cli/commands/serve_cmd.py

1. Fix --port not passed to FastMCP
   mcp.run(transport=args.transport) → mcp.run(transport=args.transport,
   port=args.port)
   Check FastMCP.run() signature first — if it accepts port as a kwarg
   pass it; if it uses a different param name use the correct one.

2. Fix FORWARDED_ALLOW_IPS for proxied requests
   When transport is sse or streamable-http, set the environment variable
   or FastMCP config so uvicorn accepts proxied requests:
   os.environ["FORWARDED_ALLOW_IPS"] = "*"
   Set this BEFORE calling mcp.run() and only for non-stdio transports.

3. Add streamable-http to help text
   Update the --transport help string from "stdio or sse" to
   "stdio, sse, or streamable-http"

4. Print DIY remote access hint when starting SSE or streamable-http
   After printing the startup line, print:

   To expose this server remotely, use your own tunneling tool:
     ngrok:        ngrok http {port}
     cloudflared:  cloudflared tunnel --url http://localhost:\{port\}
     tailscale:    tailscale funnel {port}

   Or use: snodo serve --tunnel (requires free snodo account)

   Keep it brief — one block, printed once, then the server runs.

## Acceptance criteria
- snodo serve --transport sse --port 8080 actually binds to 8080
- Requests proxied through ngrok/cloudflared/tailscale work (no
  "Invalid Host header" error)
- streamable-http appears in --help
- DIY hint printed when using sse or streamable-http transport
- stdio transport unchanged — no hint, no FORWARDED_ALLOW_IPS change

## Testing
- Unit: --port flag is passed through to the server (mock mcp.run,
  assert port kwarg)
- Unit: FORWARDED_ALLOW_IPS set for sse/streamable-http, not for stdio
- Unit: hint printed for sse/streamable-http, not for stdio
- Full suite passes

## Constraints
- Read cli/commands/serve_cmd.py and check FastMCP.run() signature
  (mcp.server.fastmcp) before touching anything
- Only touch serve_cmd.py
- Keep the hint short — engineers know what ngrok is
