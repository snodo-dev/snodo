# Spec: Coder reads before writing (lean Path-1 upgrade)

## Why

The coder is structurally blind to file contents (recon confirmed: TaskSpec carries a
directory tree + config files, never the contents of files it edits; LiteLLMAdapter
_call_llm is a raw completion with no tools). For "modify file X" it sees X's path but
never X's content, so it regenerates the whole file from the task spec + tree — which is
why a change once "fucked up index.html". It cannot honor "existing code is source of
truth / minimal diff" because it never reads.

This is the symmetric write-side of the validator fix: same bounded tool-loop over
completion_fn (LiteLLM supports tools natively), with READ tools so the coder can read
current content before generating. LiteLLMAdapter already has mcp_servers + attach_mcp_tool
(litellm.py:35,159-161) sitting unused — this wires them in.

Scope: this is the LEAN Path-1 (API/LiteLLM) coder. It gives the coder eyes (read), it does
NOT make the coder write directly — the executor still writes the returned CodeArtifact, and
the validate/token/commit flow is unchanged. A first-class coder that owns its writes
(direct write, container diff-apply) is Path 2 / the CLI-driven rebuild — a separate, larger
job. Do not do that here.

## Changes

### LiteLLMAdapter (coders/litellm.py)
- Convert _call_llm (currently a single raw completion, ~101-116) into a BOUNDED tool-use
  loop over completion_fn(tools=[...]).
- Expose READ-ONLY context tools (reuse the foundation ops added in the validator spec):
    read_file(path)
    read_file_lines(path, start, end)
    list_files(directory)
  NO write tool, NO shell. The coder still emits its result as a CodeArtifact (the existing
  output format) — it does not write files itself.
- Loop: model may call read tools to gather context; results fed back as tool-role messages;
  repeat until the model emits the final CodeArtifact JSON OR a hard cap of N turns (e.g. 6),
  then parse the CodeArtifact as today.
- The read tools call the injected workspace_mcp (LiteLLMAdapter needs a workspace read
  handle, same ops the validator loop uses).

### Output / downstream — UNCHANGED
- implement(spec) still returns CodeArtifact.
- The executor still writes the CodeArtifact, runs validators, commits. No change to
  engine/loop.py execute path.

## Constraints

- Reuses the foundation read ops from validator-readonly-tool-loop.md — that must land first.
- completion_fn signature unchanged. The validator loop and coder loop share the same
  tool-calling capability.
- Read-only tools only in this spec. Direct-write / first-class coder = Path 2, separate.
- Bounded turns. When no read is needed, the loop returns the CodeArtifact on the first
  turn (behaviour-equivalent to today for trivial tasks).
- Output format (CodeArtifact JSON) unchanged so parsing and the executor stay the same.

## Acceptance

- For a "modify existing file X" task, the coder reads X's current content (read_file)
  before producing its CodeArtifact, so it makes a faithful edit instead of regenerating
  blind. The "overwrote/clobbered an existing file" failure mode is gone.
- Output is still a CodeArtifact; executor write/validate/commit flow is unchanged.
- Coder cannot mutate the repo itself (no write tool in its set; executor owns writes).
- Bounded at N turns; trivial tasks still resolve in one turn.

## Tests

- Mock completion_fn to emit a read_file tool call then a CodeArtifact; assert the read ran,
  content was fed back, CodeArtifact parsed and returned.
- A modify-existing-file task: assert read_file is called with the target path before the
  CodeArtifact is produced.
- Bounded at N turns (cap forces a CodeArtifact).
- No-read task: returns CodeArtifact on first turn (regression — text-only path still works).
- Existing coder + engine suites pass.
