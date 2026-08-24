# ADR 021 — Recovery builds from the original task, not the previous attempt

## Status
Accepted

## Context
Each recovery cycle derived from the immediately previous attempt, producing
three symptoms with one cause:

1. **Ids nest.** `_spawn_recovery_subtask` built the fix task id as
   `{loop_state.task.id}_fix_{len(spawned_subtasks)+1}`. Because the subtask's
   own id became the parent of the next fix, the id grew recursively:
   `task_X_fix_1_fix_1_fix_1` instead of `task_X_fix_3`.
2. **Specs nest.** `_build_recovery_spec` wrapped the *previous* spec —
   `loop_state.task.spec`, which was already a recovery spec — rather than the
   original intent, so the wrapper and acceptance criteria repeated once per
   depth. At depth 3 the `meta-spec` validator rejected its own output as
   "repeated recursive text instead of an actual failure list".
3. **The failure evidence never arrives.** The quality validator captured a
   bounded stdout/stderr tail only on *operational* faults (command not found,
   not executable, timeout). A *genuine* non-zero test result collapsed the
   output to a one-line `_extract_summary` ("Tests failed (exit 2): }"), so the
   recovery spec carried no test name, no assertion, no file. The coder was
   asked to fix an invisible failure and guessed until depth ran out.

Three real cases each burned four cycles — three coder calls and nine
tool-enabled validator calls — on defects a human diagnosed in one command: an
immutable redirect response, a doubly-read `Response` body, a routing guard. In
every case the evidence existed in the test output and never reached the fix
task.

## Decision

1. **Carry the original intent once, unchanged.** `Task` gains `root_task_ref`
   and `root_spec`, set when a recovery subtask is spawned, and threaded through
   the graph state (`SerdeMixin`) and the closure driver's initial state. A
   recovery spec is built from `root_spec` — never from the previous attempt's
   spec — so it can never wrap itself.

2. **Failures accumulate, attributed to the attempt that produced them.**
   `Task.prior_failures` accumulates failure entries `{attempt, validator_id,
   severity, justification}` across attempts. `_build_recovery_spec` renders the
   whole list with each failure's attempt number, so a fix that cures one
   failure and introduces another shows both, and which attempt produced which.

3. **The evidence the validator captured is what the fix task gets.** The
   quality validator now emits the bounded stdout/stderr tail on a *genuine*
   test failure, not a one-line summary. The justification — already preserved
   verbatim by `_build_recovery_spec` — now carries the actual assertion, test
   name and file.

4. **Recovery attempts are numbered linearly.** The fix id is
   `{root_task_ref}_fix_{depth+1}`, so the chain reads `task_X_fix_1`,
   `task_X_fix_2`, `task_X_fix_3`. Ids remain load-bearing — they key the
   checkpoint, name the branch and worktree, and are bound into the validation
   token — and existing ids still resolve because `root_task_ref` defaults to
   the task id and `prior_failures` defaults to empty.

5. **A repeated verdict halts recovery.** If an attempt produces a validator
   result identical (multiset of `validator_id`/`severity`/`justification`) to
   the previous attempt's, the loop sets `halt_type: "recovery_stalled"` (a
   blocker) before spawning another subtask. Repeating the same verdict proves
   the loop cannot converge; every further cycle costs a coder call plus a full
   quorum for nothing.

## Constraints preserved

- **Re-running pre-execute validators on each cycle is unchanged.** A recovery
  spec is new content and could introduce a violation the original did not
  propose; skipping pre-execute validation would weaken the enforcement claim.
- **Existing session and checkpoint data still resolves.** The new `Task`
  fields are additive with defaults (`root_task_ref=None`, `root_spec=None`,
  `prior_failures=[]`), so previously persisted tasks deserialize unchanged.

## Consequences

- Depth-3 recovery specs contain the original intent exactly once; meta-spec
  can no longer reject its own loop's output as recursive.
- Ids are stable and linear, not nested.
- The fix task receives the same evidence the validator captured.
- A non-converging loop stops at the first repeated verdict instead of
  exhausting depth.

## Alternatives considered

- **Keep wrapping the previous spec but strip a wrapper prefix:** rejected —
  brittle (relies on detecting wrapper text) and still loses the failure
  attribution across attempts.
- **Fail on repeated verdict at the closure driver instead of the node:**
  rejected — the comparison needs the validator results and the accumulated
  failures, which live in the loop state at spawn time; doing it earlier is the
  natural place.
- **Number ids by `len(spawned_subtasks)` on the root:** rejected — the root's
  `spawned_subtasks` is not visible to a nested invocation; deriving from
  `depth` (which is already threaded) gives a stable linear number without new
  cross-node state.
