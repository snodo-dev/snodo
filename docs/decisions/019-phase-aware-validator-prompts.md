# ADR 019 — Validator prompts are phase-aware, and repository-content validators get read tools

## Status
Accepted

## Context
`LLMValidator` computed `phase` inside the tool loop and used it for exactly one
thing: stripping `read_diff_between_refs` outside post-execute. It never reached
the prompt. The judge was told "Evaluate the task against the criteria below"
and handed a filesystem, with no indication whether it was reviewing a proposal
or inspecting a finished result.

Blind, that ambiguity is harmless: with only spec text in front of it,
"evaluate the task" can only mean "evaluate this proposal". But no shipped
template declared validator-level `tools`, so every LLM judge evaluated the task
text and had never opened a file. That is correct for `meta-spec` (it judges the
spec, and the spec is all it should see) and a fail-open for any criterion
phrased as a fact about the repository — including `solo`'s "No signing material
in the repository", which shipped since 0.1.0 and could never check anything.

Granting read tools alone reproduces a defect seen on a real project: two
independent tool-enabled judges returned `blocker` on a pre-execute task because
the code did not exist yet — which is the definition of pre-execute. With
`list_files`, the same sentence reads as "check whether this was done". The
workaround in use was a hand-written "REVIEW FRAME" criterion prepended to every
protocol, which every author had to reinvent.

## Decision
Two changes, made together because each is unsafe without the other:

1. **The prompt states the phase.** `_phase_frame(phase)` is injected into both
   the tool-loop prompt and the single-completion prompt. At pre-execute the
   judge is told it is reviewing a proposal and that the absence of the
   described work is never a finding; at post-execute it is told it is
   inspecting completed work and that absence *is* a finding. This is written
   into the engine, not into every protocol author's criteria.

2. **Shipped templates grant `read_file` + `list_files` to validators whose
   criteria concern repository contents.** `security` and `architecture` in
   `solo`, `team`, and `2+n`, plus `conventions` in `2+n` (naming / file
   organization / module boundaries). `meta-spec` gets none. The read-only
   allowlist is unchanged; validators still cannot mutate anything.

## Why this is a semantic change, not an implementation detail
A criterion is a predicate over the task spec. Making the judge phase-aware
changes what that predicate ranges over: at pre-execute it ranges over the
proposal, at post-execute over the produced result. The same criterion text now
means two different things depending on phase, and the phase is part of the
protocol language (a validator's `evaluation_phase`), not an engine knob. This
is a change to the meaning of a criterion, so it is recorded here rather than in
a source comment.

## Consequences
- A tool-enabled pre-execute validator no longer cites absence of
  implementation; a post-execute validator still evaluates the result.
- Existing protocols that already declare tools behave identically — the phase
  frame is additive, and the tool allowlist is unchanged.
- Cost: the tool loop allows up to 20 turns per validator. Granting tools to two
  more validators means those validators may now make multiple LLM calls instead
  of one. The loop is bounded and read-only; a judge that needs no files can
  still return a verdict on the first turn.

## Alternatives considered
- State the phase in the criteria (the "REVIEW FRAME" workaround): rejected —
  it must be reinvented by every protocol author and is easy to omit, which is
  exactly the fail-open this ADR removes.
- Grant tools without a phase-aware prompt: rejected — reproduces the
  completion-checker defect (issue #32).
- A compiler warning for "criterion refers to repository contents but validator
  declares no tools": considered and dropped — the heuristic is not reliably
  decidable, and the shipped templates now grant tools where they are needed.
