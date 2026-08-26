# ADR 033 — Tool loop repeat read memory and result preservation

## Status
Accepted

## Context
In multi-turn coder and validator tool loops (`LiteLLMCoder` and `LLMValidator`), models frequently re-request files or directories already fetched in earlier turns (e.g. `read_file("src/scripts/auth.js")` at Turn 3 and Turn 19). Previously, the tool loop had no memory of past reads, so it re-executed disk operations and re-appended full file content blocks to the conversation messages array.

Furthermore, eliding or stripping older tool results from the messages array in-flight was considered.

## Decision
1. **Repeat Read Deduplication**:
   - The tool loop maintains an in-turn read memory (`read_memory`) mapping canonical read tool signatures `(tool_name, canonical_args_str)` to 1-indexed turn numbers (`turn_idx`).
   - When a model calls a read tool (`read_file`, `read_file_lines`, `list_files`, `git_show`, `read_diff_between_refs`, etc.) with arguments identical to a previous turn `N`, the tool loop intercepts the call without re-reading disk and returns a turn pointer:
     `"'<path>' was already fetched using <tool> in Turn N. Refer to the tool response from Turn N for its content."`

2. **Preservation of Past Tool Results (Eliding is Unsafe)**:
   - Older tool results MUST NOT be elided or removed from the `messages` array once superseded.
   - **Rationale**:
     1. Eliding or mutating past messages invalidates provider prompt caching mechanisms (e.g., Anthropic/LiteLLM/OpenAI prefix caching), causing 100% cache misses and higher latency across turns.
     2. Eliding destroys reasoning chain and context history necessary for multi-step refactoring.
     3. Strict LLM tool APIs enforce complete `assistant.tool_calls` <-> `tool.tool_call_id` message pairing.
   - Deduplicating repeat read requests at origin prevents duplicate file content from entering the transcript, keeping transcript growth linear while preserving cache hits and history integrity.
