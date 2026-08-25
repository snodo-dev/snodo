# ADR 026 — Protocol and Governance State Protected from Agent Mutation

## Status
Accepted

## Context
Snodo's central claim is mode separation and structural governance enforcement: AI agents participate as team members gated by declarative protocol rules (`.snodo/protocol.yml`). An AI agent must not be able to alter the governance policies or protocol rules by which its own work is judged.

Previously, `.snodo/` was accessible through the `WorkspaceMCP` file-writing tool surface without path protection. If an agent (or a recovery prompt pointing at `.snodo/protocol.yml`) attempted to edit `.snodo/protocol.yml`, write files into `.snodo/`, or delete files under `.snodo/`, the tool surface allowed the write to proceed if valid JWT tokens were held.

## Decision

1. **INV2 Tool-Surface Protection for `.snodo/`**:
   The workspace tool surface (`WorkspaceMCP` write, delete, and mkdir methods) and git staging (`GitMCP.stage_files`) enforce an invariant path boundary (INV2): any tool-surface mutation targeting `.snodo/` or files within `.snodo/` is refused.
   Path validation raises `PathValidationError` explicitly naming the path and explaining that paths under `.snodo/` are protected from tool-surface mutation.

2. **Read Access Preserved**:
   Read operations (`read_file`, `read_file_lines`, `list_files`, `file_exists`) targeting `.snodo/` remain fully permitted. Grounding validators or agents in protocol configuration is legitimate; only mutation through tool calls is blocked.

3. **Separation of System State Writes from Agent Tool Surface**:
   Snodo's internal system components (session checkpoints, audit log, wave registry, state writes) operate via internal Python filesystem APIs (`open`, `Path.write_text`, atomic `os.replace`) and do not invoke `WorkspaceMCP` or `GitMCP`. System state writes remain unimpeded while agent tool operations are strictly constrained.

4. **Coder Artifact Filtering**:
   Coder adapters (`LiteLLMAdapter`, `OpenCodeAdapter`, `OpenCodeCLIAdapter`, `MockAdapter`) filter out any file operations targeting `.snodo/` from generated `CodeArtifact`s. A change under `.snodo/` is never treated as a code artifact, preventing uncommitted or staged protocol modifications from reaching execution writeback.

## Consequences
- Agents cannot modify `.snodo/protocol.yml` or alter governance state through tool calls or workspace writes.
- Attempted writes, deletions, or directory creations targeting `.snodo/` raise an explicit `PathValidationError` naming the path.
- Read operations for protocol files and validator inspection function normally.
- Snodo's internal session, audit, and wave writes continue operating without disruption.
