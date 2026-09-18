# ADR 050 — A validator's repeat-read turns are free but separately bounded

## Status

Accepted.

## Context

`LLMValidator._evaluate_with_tools` bounds a judge's tool-use loop with
`max_tool_turns`: one budget slot is charged per call to the model, and the
last slot forces a verdict-only turn (ADR 033, `_VERDICT_ONLY_INSTRUCTION`).
The loop already detects a repeat read — a call whose arguments are
identical to one already answered in an earlier turn — and intercepts it
with a pointer to that turn instead of re-reading (`ReadMemoryTracker`,
`format_repeat_read_response`, ADR 033). That response tells the judge the
call "wastes a turn," and until this decision it was telling the truth in a
way that worked against the engine: the turn was charged anyway, at exactly
the moment the judge was contributing nothing. A judge that circled on a
small set of reads could spend its entire budget before the cap forced a
halt, having examined nothing new for most of it. Raising the cap does not
fix this — a judge that circles will circle through any budget.

## Decision

A turn whose tool calls are *all* repeat-read hits is not charged against
`max_tool_turns`. Progress is tracked separately (`turns_used`, compared
against the budget) from raw loop iterations: a fresh read, a refused
undeclared tool, an invalid `submit_verdict`, or narration all still charge
a turn as before — only the repeat-read interception is free.

Because repeats are free, a judge that only ever repeats itself cannot be
stopped by the budget alone — it could circle forever. A second, small,
fixed counter (`_MAX_STALL_TURNS`) counts *consecutive* turns whose only
tool calls were repeat-read hits. Once it is reached, the loop forces the
judge onto the same verdict-only turn already used at the true end of
budget and after a prose nudge: `submit_verdict` is the only tool offered,
required via `tool_choice` where the provider honors it, with
`_VERDICT_ONLY_INSTRUCTION` otherwise. That path already fails closed
(`blocker`, `error=True`) if the judge still will not decide. No new
severity, halt type, or task status is introduced — a judge that only
circles ends exactly where a judge that runs out of real budget already
ends, just sooner.

This is scoped to `LLMValidator`'s turn budget only. The repeat-read
interception and its message are unchanged, and the coder's tool loop
(`LiteLLMCoder`) is unchanged: a coder is not judging under a turn cap in
the same sense, and nothing here alters what it does with the tracker.

## Consequences

A judge that reads efficiently is unaffected: it never generates a repeat
hit, so `turns_used` tracks the raw turn count exactly as it did before this
decision. A judge that repeats a read a few times before moving on keeps
the budget it would previously have burned on those repeats, and can spend
it examining something new instead. A judge that only ever repeats itself
is stopped within a few turns of its first repeat rather than at the end of
a (possibly generous) turn cap, and still delivers a real verdict or a real
fail-closed error, never a silent, unattributed halt.

## Alternatives

Raising the cap again was rejected: it was already raised once (12 to 40)
for exactly this failure mode and did not help, because the failure is
"progress per turn," not "total turns." Silently returning the file
contents again on a repeat was rejected — it is the behavior ADR 033
deliberately replaced and re-introduces the transcript growth and cache
pressure that decision exists to avoid. Adding a new halt type or severity
for "the judge stalled" was rejected: `blocker`/`error=True` from
"no verdict" already covers it, and the engine's halt vocabulary is closed.
