# Spec: Coder structured file output (submit_files), mirror of submit_verdict

## Why

Recon confirmed the coder's file output is 100% free-text: after the read-tool loop,
msg.content is json.loads'd into a JSON array of {path, content} (litellm.py:176-177,
295-301). max_tokens=4000 with NO finish_reason check and NO logging. A large file
(ingest.ts) embedded as a hand-escaped JSON string overruns the cap, truncates
mid-string -> invalid JSON -> ParseError, while the job reports completed/exit 0 with no
file written. Plus hand-escaping fragility and a fence-regex bug (non-greedy `.*?` stops
at the first ``` inside file content).

This is the write-side of exactly what submit_verdict fixed for validators: unstructured
output that won't reliably parse. The cure is the same — a structured terminal tool.

## Changes (coders/litellm.py)

### (a) submit_files terminal tool — mirror submit_verdict
- Add a `submit_files` tool definition: params `files`: array of
  {path: string, content: string, action: enum ["write","delete"]}.
- Loop terminates when the model calls submit_files: read tc.function.arguments
  directly and build the CodeArtifact from the structured file list. NO free-text
  parsing on the happy path. The SDK carries content as a tool argument, so the model
  no longer hand-escapes a giant JSON string.
- Check submit_files FIRST in the turn, before executing read tools (same pattern as
  submit_verdict in validators/llm_validator.py — use it as the reference impl).
- Prompt: instruct the model to read what it needs, then deliver ALL file operations via
  a single submit_files call. Do not emit file content as prose or as a JSON text blob.

### (b) Retry once, then fall back
- If a turn ends with free-text and no submit_files (and no tool calls), inject one
  corrective turn: "Deliver your changes by calling submit_files(files=[...]). Do not
  emit them as text." Allow one more turn.
- After retry, fall back to the legacy free-text parse (for models that won't tool-call),
  with the regex fix and logging below. If that also fails, ParseError with the raw
  response logged.

### (c) max_tokens bump + truncation detection
- Raise default max_tokens 4000 -> 16000 (large / multi-file headroom).
- Add a finish_reason check: if the completion stopped due to length (truncation), raise
  a DISTINCT clear error ("coder output truncated at max_tokens — raise limit or split
  task"), never silently produce malformed/partial output. Truncation must never be
  invisible again.

### (d) Pre-parse logging
- Add a module logger (none exists today). Before any ParseError and on truncation, log
  the raw response truncated to ~2KB so failures are debuggable from logs.

### (e) Fix the fence-extract regex
- The fallback fence extractor `r'```(?:json)?\s*\n(.*?)```'` uses non-greedy `.*?` and
  breaks when file content itself contains ```. Fix it (match to the last fence / strip
  only the outermost fence) so content with backticks doesn't corrupt extraction.

## Constraints

- Mirror the submit_verdict pattern exactly: structured terminal tool, zero free-text
  parsing on the happy path.
- Output still becomes a CodeArtifact; the executor still writes; validate/token/commit
  flow unchanged.
- Backward compat: the no-tools single-completion path keeps free-text parse (with the
  regex fix + logging).
- Truncation is always a clear error, never a silent no-write.

## Acceptance

- A large file (ingest.ts) is delivered via submit_files and written correctly — no parse
  failure, no silent no-write.
- A multi-file task delivers all files in one submit_files call, one turn.
- A truncated completion raises a clear truncation error, not a silent "completed exit 0".
- On any parse failure the raw coder response is logged (truncated).
- File content containing ``` no longer breaks extraction.
- Small-file tasks and the no-tools path still work (regression).

## Tests

- Model calls submit_files with multiple files -> CodeArtifact from structured args, no
  free-text parse, files written.
- Large file content via submit_files -> no truncation, written correctly.
- finish_reason=length -> distinct truncation error (not ParseError, not silent).
- Free-text without submit_files -> retry -> submit_files on retry -> success.
- Legacy free-text path: content containing ``` parses correctly (regex fix).
- Raw response logged on parse failure.
- Existing coder / engine suites pass.
