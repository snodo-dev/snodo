# Spec: Protocol-declared validator tool access (replaces phase gating)

## Why

The validator tool loop we shipped is gated on phase == post_execute. But the
validator that needs it — adr — is and must remain PRE-execute: ADR conformance
gates the work before the coder runs, not after. And the ADR registry grows, so
docs/adr/ can't be pre-loaded into context — it must be read on demand.

So tool access must be a per-validator capability declared in the protocol,
decoupled from phase. Phase only determines what DATA exists (no diff pre-execute),
not whether a validator can read files.

This AMENDS validator-readonly-tool-loop.md: the phase gate is replaced by a
declared-tools gate.

## Changes

### 1. Validator model (compiler/models.py:72-94)
- Add `tools: List[str] = Field(default_factory=list)` — a read-only tool allowlist.
- Add a field_validator that rejects any name not in a module-level fixed read-only
  set `_READ_ONLY_TOOL_NAMES = {read_file, read_file_lines, list_files, git_show,
  git_log, read_diff_between_refs}`. No write/exec/mutating tool name is ever accepted.
  Protocol load fails loudly if a validator declares an unknown or non-read-only tool.

### 2. Tool-loop activation (validators/llm_validator.py:74-80)
- Replace the phase gate. The loop runs iff:
    self.validator_spec.tools is non-empty
    AND workspace_mcp/git_mcp present AND completion_fn present
- Empty/absent tools => single-completion path (no loop, no tools). This is the
  default for security/architecture — unchanged behaviour.
- DO NOT fall back to "empty => full set." Empty means no loop. Explicit grant only.

### 3. Toolset assembly (llm_validator.py:_build_tool_definitions)
- Expose exactly the declared tools (intersect validator_spec.tools with the fixed
  read-only set). Never the full set by default.

### 4. Phase filter (llm_validator.py, in _evaluate_with_tools)
- `read_diff_between_refs` is only meaningful when a change is committed. Strip it
  from the toolset when context.phase != "post_execute". All file/git-read tools
  (read_file, read_file_lines, list_files, git_show, git_log) are available at BOTH
  phases.

## Constraints

- Read-only invariant: the fixed set is read-only only; the field_validator makes it
  impossible to grant a write/exec tool. Validators never mutate.
- Activation is by declared tools, NOT phase. Phase only filters the diff tool.
- No changes to ValidatorContext, engine loop validator_fn, or QualityValidator.
- Backward note: any EXISTING post-execute LLM validator that relied on the phase-gated
  loop must now declare tools: explicitly. (There are none in current protocols — quality
  is a QualityValidator subprocess runner, not an LLM tool-loop validator — so no
  migration is needed, but state this so it's intentional.)

## Acceptance

- A pre-execute validator with `tools: [read_file, list_files]` runs the bounded loop,
  can list_files(docs/adr) + read_file the ADRs on demand, and judges the task against
  them — at pre-execute, gating the coder. No diff tool is offered (none exists yet).
- security/architecture (no tools) → single completion on the task input, exactly as
  before. No tools passed to completion_fn.
- A validator declaring a write/exec/unknown tool → protocol load fails with a clear error.
- A post-execute validator declaring tools incl. read_diff_between_refs → gets the diff
  tool too.

## Tests

- Pre-execute validator with tools: loop runs, file-read tools available, diff tool NOT
  offered, can read a file via the loop.
- Validator with empty tools: single completion, no `tools` kwarg to completion_fn.
- field_validator rejects a non-read-only / unknown tool name at model construction.
- Post-execute validator with tools incl. diff: diff tool offered.
- Existing validator + server suites pass.
