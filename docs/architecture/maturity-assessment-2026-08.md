# Architecture maturity: a blunt assessment

Date: 2026-08-25
Basis: one week of hard use across two repositories (snodo itself, and a greenfield
project built exclusively through `snodo run`), 45 issues closed, 5 open, ~2,400 unit
tests, 119 e2e tests, 6 import contracts, 29 ADRs.

This is written to be useful, not encouraging. Where the design is good it says so
plainly, because the good parts are load-bearing and worth protecting.

---

## The one-paragraph verdict

**The model of governance is sound and, on the evidence, essentially unbreached. The
machinery around it is late-prototype.** Every defect found in a week of adversarial
use was in the harness — never in protocol compilation, capability binding, the audit
chain, or session resumability. That is a genuinely good result and it is the thing to
protect. But the harness is large, and its defects share one recurring shape: *a
safety property degrading to a warning, or an operational fault reported as a
judgement*. The most consequential instance is that several of the system's own gates
were reporting green while not gating. A process tool whose gates do not gate is not
yet a process tool.

---

## By dimension

### Core protocol model — **solid**

Policy-as-data, capability bounded by the active mode, invariants INV1–INV5, and
well-formedness checked at load. Not one incident in a week of use. WF1 was *relaxed*
(ADR 017) on the grounds that the formalism was stronger than the principle it cited,
which is a sign of a model being reasoned about rather than defended.

The greenfield protocol validated the central design claim in the most direct way
available: phases as modes, with hard boundaries and deliberately overlapping tool
sets, solved a real failure (`solo` applying feature-development validators to an
empty repository) that no amount of bug-fixing would have solved.

### Enforcement mechanisms — **solid**

Capability is bound at the tool surface, not in the coder. `WorkspaceMCP` and `GitMCP`
refuse out-of-bounds paths; `.snodo/` mutation is refused at that surface and, for
coders that bypass it entirely, detected by a content snapshot in the adapter base
class (ADR 026/027). Putting that enforcement one level up rather than per-adapter has
already paid off — it is the only recent capability the opencode adapters have,
precisely because they could not opt out of it.

This is also what makes the delegation strategy defensible: an untrusted third-party
generator is architecturally acceptable because the boundary is not enforced by the
generator's good behaviour.

### Self-verification — **fragile. This is the systemic weakness.**

Gates found reporting green while not gating, in one week:

| Gate | How it lied |
|---|---|
| `ruff` | Unbounded `>=0.1.0` resolved 0.15.16 in one worktree and 0.16.4 in another — 0 errors versus 1,909 for identical code. Then, after pinning, main was left lint-broken twice because two agents reported "verified" without running it. |
| `.importlinter` | Reported contracts kept while not enforcing them |
| e2e suite | Passed while a test mutated the suite repository's own branch |
| `check-bindings` (nfc) | Verified the *source* declares its bindings; nothing checked the deployment received them. A missing D1 binding reached production as a user-visible 503. |
| `quality` vs acceptance | `make check` passes when a new feature arrives with no tests. A task merged with two of three acceptance criteria unmet, all validators green. |

These are not five unrelated bugs. They are one pattern: **verification that measures
the artifact rather than the property.** Declaring a dependency is not having it;
declaring a binding is not deploying it; a passing suite is not a covered feature.

The `acceptance` validator (ADR 028) is the first deliberate attack on this and it is
the right idea. It is also **unproven — it has not yet been observed rejecting
anything**, and the standing empirical finding is that read-only judges pass
everything. Until it fires on a real miss it should be treated as an unvalidated
fourth judge, not as a closed gap.

### Integration seams — **fragile**

Covered in full in `coder-adapter-contract.md`. In short: the coder seam is implicit
(an ABC, two opt-out booleans, and `hasattr` duck typing), which makes divergence
undetectable by construction. The cost is already realised — on the container opencode
path, post-execute validators are handed `HEAD~1..HEAD`, which resolves to the
*previous* commit, so they confidently review the wrong change and pass.

The generalisable lesson: **opting out of a mechanism currently carries no obligation
to discharge what that mechanism did.**

### Observability and diagnosability — **working, improving fast**

A week ago a run was a dead screen followed by an undifferentiated failure. Now:
node transitions, per-turn tool summaries with elapsed time, validator verdicts,
halt payloads carrying cited criterion text, and hints that name the applicable fix
target rather than one of three.

The effect is measurable rather than cosmetic. Making the nfc account-card route
distinguish its failure modes (ADR 020) turned an undiagnosable 404 into a 503, then a
log line naming `card-read-failed`, and the root cause — an unapplied migration — fell
out in minutes after an afternoon of guessing. Turn-level progress (#51) explained a
truncation failure the same day it shipped.

**This is the highest-return work done all week** and the pattern is worth
generalising: uniform error responses adopted for security reasons should be scoped to
where a secret is actually at stake, and nowhere else.

### Error taxonomy discipline — **mixed**

The four-outcome contract (`halt_type == final_decision == raw_halt_type`) is a good
piece of design. It is also violated in practice: three times in one day a run emitted
a complete structured payload and then printed `✗ Internal error during execution:
unknown internal error`. One run, two outcomes, the second unclassified.

Separately, diagnoses are being *inferred* and reported as *observed*. "Task is too
large" was asserted three times when what actually happened was a truncated tool call
whose arguments exceeded the output budget. That misattribution sent the operator
looking in the wrong place each time. Rule worth adopting: report what was observed;
label inference as inference.

### Recovery and cost control — **working**

Kleene-closure recovery reached a passing result twice where a halt would have wasted
the run; stall detection fired once, stopping after two identical verdicts. Depth is
now resolvable per mode (ADR 029), which correctly located the variable — early
greenfield phases fail for reasons recovery cannot fix, `build` does not.

The remaining weakness is upstream: recovery cannot help when the fault is
misdiagnosed, and misdiagnosis is currently common (see above).

### Decision records — **solid, and unusually so**

29 ADRs in snodo, 21 in the greenfield project, with supersession handled properly
rather than by editing history. The `architecture` validator reading
`docs/decisions/` and citing records by name is a real mechanism, not documentation
theatre — it blocked a task because a stored card had to carry a template id while
the schema had no such column, before a line was written.

Two ADR-number collisions in one week from parallel agents is a process gap, not a
design one, and it is filed.

### Test strategy — **mixed**

~2,400 unit and 119 e2e tests is real investment, and property-based tests are
appearing where they belong (the tool-response invariant in #53). But:

- coverage did not prevent any of the gate failures above, because the tests assert
  the same artifact the gates do;
- adapter tests were not updated by three consecutive changes to adapter-facing
  behaviour;
- `.snodo/` blocker behaviour is tested only through synthetic subclasses, never
  against the real adapters;
- the plan-execution path had **no** coverage until this week, and writing it
  immediately surfaced three real defects — including multi-wave plans silently
  skipping every dependent wave.

That last one is the tell. Where tests were absent, defects were present and had been
for some time.

### Operational readiness (unattended) — **not ready, and the evidence is weak in both directions**

Every result this week was reviewed by a human before it was accepted, and that review
caught things the pipeline passed: two of three acceptance criteria unmet on a merged
task, dead code from a "verified" change, lint broken on main twice, an orphaned
commit silently discarded by a stale-branch reset.

That is an honest confound, and it cuts both ways: **there is no evidence the system
operates safely unattended, because it has not been operated unattended.**

---

## The gate I would set instead of a judgement call

Track the rate at which a `completed successfully` survives human inspection
unchanged. It is cheap to measure and it is the only number that matters for the claim
you eventually want to make.

This week that rate was poor. The QR task alone failed it. Run a stretch where it is
consistently high — with acceptance criteria enforced, the gates fixed, and the coder
seam contracted — and the readiness argument makes itself from evidence rather than
optimism.

---

## What I would fix, in order

1. **Make the gates gate.** Every verification must measure the property, not the
   declaration. Start with CI actually running the four commands on agent branches —
   main was lint-broken twice this week and both times a human found it by hand.
2. **Prove the `acceptance` validator fires.** It is the designated answer to the
   biggest hole and it is unvalidated. One observed rejection changes its status.
3. **Contract the coder seam** and fix the container-commit defect, which is currently
   letting validators review the wrong change.
4. **Fix the error-taxonomy leaks** — the double-reported `internal_error`, and
   inferred causes stated as observed facts.
5. **Only then** consider cloud. Cloud multiplies harness surface on top of the layer
   that has produced one hundred percent of this week's defects, and its value
   proposition depends on unattended trust that the current evidence does not support.

---

## What is genuinely defensible today

- snodo enforces a compiled protocol with hard mode boundaries, bounded capability,
  and a tamper-evident audit trail, and that enforcement held throughout a week of
  hard use.
- It is coder-agnostic by design, and that is a stronger and more testable claim than
  anything about its own coding ability: *the same protocol, the same task, two
  different generators, the same enforcement outcome.* Worth building the experiment
  that demonstrates it.
- Phases-as-modes is a real result. Applying build-phase validators to scaffold-phase
  work is a named, reproducible failure with a working fix.
- The strongest empirical finding of the week is worth stating in the paper because it
  is uncomfortable and non-obvious: **every defect that was caught was caught by the
  one validator that executes something.** Judgement scales badly; execution scales
  well. A protocol should spend its budget on things that run, and reserve judges for
  questions no command can answer.

What is not defensible today is governed autonomy — a green result you can accept
without looking. That is a matter of the harness, not the model, which is the good
news.
