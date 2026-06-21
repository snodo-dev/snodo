# Spec: Read-only ops + post-execute validator tool loop

## Why

Post-execute LLM validators are blind — they get task spec + criteria text only,
never the actual change or any file contents (recon confirmed: llm_validator.py
single completion, ValidatorContext has no MCP access). So they validate the task
description, not the code. The adr validator blocks every task because it's told to
read docs/adr/ but is handed zero files.

Two facts from recon shape the fix:
- completion_fn (litellm.completion) already supports tool-calling natively. A bounded
  tool loop touches ONLY the validator; the coder's use of completion_fn is unaffected.
- read_diff() is USELESS at post-execute: it diffs working-tree-vs-HEAD, but the
  executor already committed, so the tree is clean and it returns empty. The validator's
  "what changed" view must be HEAD~1..HEAD via a new op.

Scope: POST-execute validators only. Pre-execute validators validate the input and stay
single-completion — do NOT change them. Do NOT add a static files:[] field — the loop
is on-demand.

## Part 1 — Foundation read-only ops

### GitMCP (mcp/git.py) — new READ-ONLY methods
- diff_between_refs(ref1, ref2) -> `git diff ref1..ref2` (this is the real "what changed"
  view; HEAD~1..HEAD at post-execute)
- show(ref, path) -> `git show ref:path` (read a file's content at a ref)
- log(n=5) -> recent commits, oneline

These are read-only. Do not alter existing write ops. (checkout/get_current_branch are
known-missing but belong to the branch-per-task spec, not this one.)

### WorkspaceMCP (mcp/workspace.py) — partial read
- read_file_lines(path, start, end) -> partial file read (read_file currently full-file only)

### ValidatorContext (validators/context.py)
- Inject workspace_mcp and git_mcp references (currently absent) so a validator can call
  read-only ops. Populated where the engine builds ValidatorContext (loop.py ~740-754).

## Part 2 — Post-execute validator read-only tool loop

### LLMValidator (validators/llm_validator.py)
- Add a bounded tool-use evaluation path used ONLY for post-execute validators.
- Inject the change by default: prepend diff_between_refs(HEAD~1, HEAD) into the first
  prompt so the common case ("review this change") needs no tool calls.
- Expose a READ-ONLY toolset to the model via completion_fn(tools=[...]):
    read_diff_between_refs(ref1, ref2)
    git_show(ref, path)
    git_log(n)
    read_file(path) / read_file_lines(path, start, end)
    list_files(directory)
  NO write, NO shell, NO exec tool in the set.
- Bounded loop: execute tool calls via the injected workspace_mcp/git_mcp, feed results
  back as tool-role messages, repeat until the model returns the final JSON verdict OR a
  hard cap of N turns (e.g. 6) is hit. On cap, force a verdict from what it has.
- Verdict-only output: same JSON verdict shape the current single-completion path returns.

### Phase gating
- Pre-execute -> existing single-completion path, unchanged.
- Post-execute -> the new tool-loop path.

## Constraints

- completion_fn signature unchanged (LiteLLM already accepts tools). Coder unaffected.
- Read-only by construction: the validator toolset contains zero mutating ops.
- "What changed" = HEAD~1..HEAD via diff_between_refs. Never rely on read_diff() at
  post-execute (returns empty after commit).
- Bounded turns + verdict-only output. No iteration beyond the cap.

## Acceptance

- A post-execute validator sees the just-committed change (diff HEAD~1..HEAD) and can
  read files / ADRs on demand.
- The adr validator, with docs/adr/ present, now validates against actual ADR contents
  instead of blocking blindly.
- A validator cannot mutate the repo (no write/exec tool exists in its set).
- Bounded: at most N tool calls, then a verdict.
- Pre-execute validators behave exactly as before.

## Tests

- diff_between_refs returns the committed change for HEAD~1..HEAD; show/log/read_file_lines
  return expected content.
- Validator tool loop: mock completion_fn to emit a tool call then a verdict; assert the
  tool ran read-only, results fed back, verdict returned, loop bounded at N.
- adr validator with docs/adr/ ADRs present: validates against real content (pass/block on
  merit), no longer blocks for "not in context".
- Pre-execute validator path unchanged (regression).
- Existing validator + server + jobs suites pass.
