# ADR 048 — A coder report is best-effort evidence; it carries a shape, never a verdict

## Status

Accepted.

## Context

The engine hands a coder a prompt, waits, and then reads the worktree.
Everything else about a run is inference. From the outside the engine cannot
tell a coder that finished and deliberately found nothing to change from one
that ran out of turns, one that ran out of context, or one that simply
stopped. Every diagnosis of a failed run is therefore archaeology on a prose
tail, and the engine is not allowed to parse prose to reach a verdict — ADR
045 rejects precisely that move for a judge's output, and the same rule holds
for a coder's.

`docs/architecture/coder-adapter-contract.md` §5 established the way out of
this class of problem: a coder's obligations are made visible by a
*declaration the adapter makes about itself*, not by inference the engine
guesses at. `honoured_settings` (ADR 046 follow-on, #311) is the recent
example. This ticket adds the next such declaration, one level up: a
structured account of what a run did and how it ended.

The report is not a mechanism any coder uses yet. This decision fixes only
the **shape** and the **rule about what the shape may never be**; later tickets
make adapters fill it and let the engine read it. Deciding the vocabulary and
the constraint *now*, before the first reader exists, is the point: a shape
designed around one coder's output and a vocabulary that accretes one value per
incident are both worse than one thought through at the seam.

## Decision

Add `snodo/coders/report.py` with a `CoderReport` shape carrying three
sections — what the coder did, how far it got, and why it stopped. No adapter
populates it and nothing reads it yet.

### Every field is optional

A report is best-effort evidence from a non-deterministic participant: a coder
may do the work and forget to report, report only part of it, or report
something that did not happen. Absence is therefore normal and never an error.
The shape has no required field, and a completely empty report is valid.

### A malformed report is discarded with a log line, never a halt

`parse_coder_report` returns a `CoderReport` for well-formed input and `None`
for anything else — wrong shape, an unknown stop reason, a wrong type, an
unexpected key — having emitted one warning naming why. It never raises. A
discarded report is treated **exactly** like an absent one: to everything
downstream, a coder that garbled its report is indistinguishable from a coder
that said nothing. Halting a run the worktree could otherwise have judged on
its own merits, because a participant mis-stated its own account, would let the
least reliable signal in the system veto the most reliable one.

### The vocabulary of *why a run stopped* is closed

`stop_reason` is one value from a fixed set. The five proposed members each
earn their place by naming a state the engine cannot otherwise distinguish and
that it would otherwise have to infer from prose:

- **`completed`** — the coder believes it finished its work. This is the
  positive case the whole ticket exists to name: the alternative to a coder
  saying "I'm done" is the engine guessing it from a silent exit. `completed`
  covers both "I finished and changed files" and "I finished and found nothing
  to change" — the file list, not a separate reason, tells those apart, so the
  vocabulary stays at one member for the finished case.
- **`turn_budget`** — the coder stopped having used the turns it had. It maps
  to the `TurnBudgetExhausted` fault the litellm tool loop already raises, so
  the concept is already real in the engine; a coder running its own loop has
  no channel to report the same fact, and this is it.
- **`context_budget`** — the coder stopped against its token/context window.
  It is kept separate from `turn_budget` because the two have different
  remedies: more turns cures the first and does nothing for the second, which
  needs a smaller task or a larger window. Collapsing them would reproduce the
  inert-setting trap ADR 046 closes — one knob that looks like it should fix
  what it cannot.
- **`provider_fault`** — the coder stopped because its provider failed it
  (a 5xx, a dropped connection, a refusal to complete), not because of
  anything about the task. It belongs for the same reason the operational halt
  family exists at all (ADR 015): a fault the coder did not cause is a
  different thing from a budget it exhausted, and folding the two together
  would blame the coder for its upstream.
- **`abandoned`** — the coder stopped for a reason that is none of the above
  and that it does not (or will not) name: it simply quit. It is the closed-set
  floor. Without an explicit "I stopped and am not giving you a reason" every
  silent stop would have to be pushed into `completed` (a lie that turns a
  non-event into a claim of done-ness) or left absent (indistinguishable from
  a coder that finished honestly and forgot to report). `abandoned` makes the
  deliberate-but-unexplained stop a value in its own right instead of corrupting
  the two honest neighbours.

Two candidate members are considered and **rejected** as vocabulary growth:

- **A timeout / wall-clock stop reason.** A coder killed on the operator's
  clock never returns to fill a report — the process is gone. A run that timed
  out is recognised by the engine's own timeout handling
  (`CoderTimeoutError`, ADR 015's operational family), which does not need a
  coder to confess to it. A `timeout` member would be a value no coder is
  alive to set, and a vocabulary field that cannot be populated is the field
  that gets invented wrong later.
- **A `no_changes` / `nothing_to_do` reason.** This is `completed` with an
  empty file list. A distinct member would encode a conclusion ("the task
  needed no code") that the coder is in no position to establish — whether the
  worktree is unchanged because the work was done or because the coder gave up
  is a judgement for the validators and the diff, not a self-report.

### The report is evidence, never a verdict — and the shape enforces it

The `CoderReport` type carries no `passed`, `severity`, `status`, `verdict`, or
`halt` field, and gains none. This is a structural constraint, not a guideline,
because a rule stated only in prose is the kind a later reader edits under
pressure to make a failing run pass. Two invariants are written into the type's
own documentation and pinned by tests:

1. **The worktree is the only authority on what was written.** A file a coder
   claims but that is not in the diff was not written; a file it omits but that
   is in the diff was. The report's file list never adds to or subtracts from
   the change the engine reads back (`_read_changes_from_disk`).
2. **Nothing a coder reports may cause a pass.** `completed` is a coder's
   account of *stopping*, not the protocol's conclusion that the task was
   *done*; the validators decide that, independent of the coder (ADR 039). A
   coder reporting nothing is indistinguishable, to any decision, from a coder
   that reports — because neither can move the verdict.

Mapping a stop reason onto a halt type, an outcome, or any downstream decision
is **out of scope here** and is a deliberate, separate later decision. This ADR
does not make one; it makes the shape incapable of quietly deciding anything
in the meantime.

### What this ticket does not do

- No state, severity, halt type or task status is added.
- No stop reason is mapped onto a halt.
- Coder stdout/stderr is not parsed to populate any field; a coder that wants
  to report does so through this shape, or not at all.
- No adapter produces a report yet.

## Consequences

- The engine has a place to receive a structured account of a run, so the next
  tickets can make coders fill it and the engine read it without inventing the
  vocabulary under the pressure of a single failing run.
- The vocabulary is fixed now, while nothing depends on it, so widening it
  later is the deliberate act it should be (the same ratchet discipline
  `scripts/enforce_vocabularies.py` applies to severities, halts and statuses).
- The "evidence, never a verdict" property is enforced by tests
  (`test_report_type_has_no_verdict_bearing_field`,
  `test_stop_reason_literal_and_runtime_set_agree`), so a future edit that
  tries to make a coder's self-report decide something fails at the branch.
- A coder that mis-reports — over-claims files, lies about finishing — is
  harmless to the verdict by construction: the worktree and the validators are
  unchanged, and the false claim is only ever additional (discardable) context.

## Alternatives considered

- **Populate the report by parsing the coder's stdout/`--format json` tail.**
  Rejected: this is the archaeology the ticket exists to remove, and it drags
  the engine back into reading prose for a conclusion (ADR 045). A coder that
  emits a structured report is a coder choosing to; a coder whose prose is
  mined for one is not.
- **Make the report a required part of the adapter contract now.** Rejected:
  the participant is non-deterministic and several coders cannot honestly
  produce every field; a required field becomes a fabricated field the moment
  it is required. Best-effort is the only honest contract here.
- **Let a `completed` report short-circuit validation when the coder says it is
  done.** Rejected outright: that is the misuse the whole type is built to make
  impossible. Validation is independent of the coder (ADR 039) and a coder's
  word cannot satisfy a gate meant to judge its work.
- **A wider stop-reason set with `error`, `unknown`, or `timeout` members.**
  Rejected for the reasons above: `abandoned` is the honest floor, a wall-clock
  timeout is the engine's to see not the coder's to say, and an `unknown` member
  duplicates the absent case that already means "the coder did not say."
