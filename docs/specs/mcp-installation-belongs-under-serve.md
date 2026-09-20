# MCP Installation Belongs Under Serve

## Intent

MCP client registration is an operation on the server this project serves, not
an installation of the `snodo` executable. The supported interface therefore
lives under `snodo serve`.

## Contract

- `snodo serve --mcp-install` registers the project's stdio server entries.
- `snodo serve --mcp-uninstall` retains mode selection, purge, orphan cleanup,
  and confirmation controls.
- `snodo serve --mcp-uninstall-all` removes every snodo-managed entry.
- `snodo install` and `snodo uninstall` remain working deprecated spellings and
  name their corresponding `serve` replacements.
- Both interfaces write identical client configuration bytes.
