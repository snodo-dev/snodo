---
adr: 009
status: Accepted
---

## 009: FastMCP over custom MCP transport

- **Status**: Accepted
- **Context**: Decisions.md records (2025-05-26): "What we built that we shouldn't have: Custom MCP transport (Task 4.5 fixes)." The original implementation had a hand-rolled MCP server transport. FastMCP is the established Python MCP server library.
- **Decision**: Replace the custom transport with FastMCP (`transport.py:24,39-62`). Each `ProtocolMCPServer` is mounted as a FastMCP server named `snodo-{protocol_id}` (all modes) or `snodo-{protocol_id}-{mode_id}` (single mode). Tools are registered via `mcp.add_tool(fn, name=..., description=...)`.
- **Consequences**: Tool registration is declarative. FastMCP handles stdio transport, shared server infrastructure, and MCP protocol compliance. The `snodo serve` command accepts `--mode` to serve a single mode's tools or omits it for all modes.
- **Alternatives considered**: Custom transport — rejected; recorded in decisions.md as a mistake. Third MCP library — not evaluated; FastMCP is the standard.
- **Evidence**: Audit log entry 26 (2025-05-25, Task 4.5), commit `238bbc5a`; `decisions.md:48`; `transport.py:24,39-62`.

---
