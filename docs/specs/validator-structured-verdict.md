# Spec: Structured validator verdict + honest halt labels

## Why

adr read the ADRs correctly (tool access works) but the tool loop lets the model end
on free-text. With max_tokens=500 it narrated and truncated before emitting JSON →
parse failure → defaulted to warn → unanimous escalated → mislabeled "✗ BLOCKED: blocker"
(blocker_count was 0). Three defects: free-text verdict parsing is fragile, a parse
failure becomes a halting warn, and an escalation is mislabeled as a blocker.

disagreement_policy stays "unanimous", UNCHANGED — warn and blocker both still halt.
This spec only makes the verdict robust and the labels honest. It does NOT weaken policy.

## Changes

### (a) Structured verdict via a terminal tool (validators/llm_validator.py)
- Add a `submit_verdict(severity, justification)` tool to the tool-loop toolset; severity
  enum ["pass","warn","blocker"].
- Loop terminates when the model calls submit_verdict: read args directly
  (json.loads(tc.function.arguments)) -> ValidatorResult. NO free-text parsing on the
  happy path.
- Read tools (read_file, list_files, etc.) still callable in earlier turns; submit_verdict
  is the terminal action.
- Raise max_tokens on the tool-loop completion (currently 500 at llm_validator.py:198) to
  ~1500 so reasoning + the verdict tool call don't truncate.
- Prompt: instruct the model to call submit_verdict with its final verdict once it has
  read what it needs; do not narrate the verdict as prose.

### (b) Retry once on missing/invalid verdict
- If a turn ends with free-text and NO submit_verdict call (or invalid args), inject ONE
  corrective turn: "Return your verdict by calling submit_verdict(severity, justification).
  Do not narrate." Allow one more turn.

### (c) After retry, fail CLOSED as a distinct validator error
- If still no valid verdict after the retry, return severity="error" (add to
  VALID_SEVERITIES at llm_validator.py:50).
- OVERRIDE the recon: error must NOT be excluded-from-escalation / proceed. A malfunctioning
  validator must never silently bypass the gate. `error` HALTS fail-closed with
  halt_type="validator_error", surfaced for human resolution (snodo resolve).
- policy.py: count `error` separately; it does not satisfy unanimous (not a pass) -> halt,
  but it is tagged validator_error, not escalated-warn and not blocker.

### (d) Honest halt labels (cli/run_cmd.py)
- Key the display off halt_type, not is_blocked alone (run_cmd.py:663-665 and the structured
  reason at run_cmd.py:572):
    halt_type="blocked"         -> "✗ BLOCKED: <constraint_violations>"
    halt_type="escalated"       -> "✗ ESCALATED (warn): <validator warnings>"
    halt_type="validator_error" -> "✗ VALIDATOR ERROR: <validator> produced no verdict — resolve or retry"
- An escalated warn must never print "blocker".

## Constraints

- Policy unchanged (unanimous). warn and blocker halt exactly as before.
- Governance integrity: a validator that errors HALTS (fail-closed), never silently bypassed.
- Happy path does zero free-text verdict parsing — submit_verdict args are structured.
- No transport / MCP / model changes.

## Acceptance

- adr, after reading the ADRs, calls submit_verdict and returns a clean pass/blocker. The
  conforming wrangler.toml task passes pre-execute under unanimous and proceeds.
- A narrated/truncated response no longer yields an accidental verdict: it triggers one
  retry, then a clearly-labeled validator_error halt — never a silent warn, never
  "BLOCKED: blocker".
- An escalated warn renders as "ESCALATED (warn)", never "BLOCKED: blocker".
- A validator error never lets a task proceed silently (fail-closed).

## Tests

- Model calls submit_verdict -> structured verdict, parse path never hit.
- Model narrates without submit_verdict -> one corrective turn injected -> submit_verdict
  on retry -> verdict.
- Model never calls submit_verdict even after retry -> severity="error",
  halt_type="validator_error", task HALTS (not proceed, not warn).
- max_tokens raised on the tool-loop path.
- Display: blocked / escalated / validator_error render distinct labels; escalated never
  prints "blocker".
- policy: error halts under unanimous and is not counted as a pass; warn/blocker behaviour
  unchanged.
- Existing validator / policy / run_cmd suites pass.
