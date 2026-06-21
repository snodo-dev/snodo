# TASK: Wave classifier — stop fragmentation

> Status: implemented (262 tests pass, ruff clean).
> R5's model contract was superseded by `fix-wave-classifier-model-config.md`
> (hardcoded model removed in favor of an `llm.classifier` config section).

## Goal

The wave classifier currently degenerates to one wave per task. The matching
mechanism (an LLM comparing a new task against open waves) is sound and stays.
The job is to make matching actually work by fixing input quality, removing a
self-defeating prompt bias, and making failure modes fail safe instead of
silently minting new waves.

Success = related tasks reliably land in the same wave, and no failure path
ever silently invents a wave.

## Scope

Python OSS engine only:
- `wave_registry.py`
- `config.py`
- `engine/loop.py`

Out of scope (do not touch): cloud workers, the combined flow_type+wave
single-call design (keep it), expiry semantics (14d age / 5d idle stay),
embedding/vector infrastructure.

## Why (context, not instructions)

Recon confirmed five compounding causes. The task must address all five:
1. Wave identity is built from raw task-spec slices, so the classifier compares
   new work against unreadable garbage and can't match.
2. The prompt explicitly tells the model to mint new when uncertain — and with
   garbage descriptions it is always uncertain.
3. JSON parsing is fragile and any failure falls back to "new".
4. A bare `except: pass` in the loop drops classification failures silently.
5. Classification runs on the expensive coder model.

## Behavioral requirements (contracts)

**R1 — Clean wave identity.**
The text persisted to represent a wave (its description and any anchor/summary
fields used for matching) must be a concise, human-meaningful summary. It must
never be a verbatim slice of a task spec. If the classifier does not return a
usable summary, fall back to a meaningful field such as the task title — never
to `task_spec[:N]`.

**R2 — Neutral matching bias.**
The prompt must not lean toward minting new *or* toward matching. The decision
is evidence-driven: match when the task genuinely shares a feature area with an
open wave; mint otherwise. Remove any "if uncertain, return new" style
instruction. (Rationale: false-mint inflates velocity but is recoverable;
false-match corrupts cycle time and is hard to detect. With clean input,
"uncertain" should be rare — don't put a thumb on either scale.)

**R3 — Never silently mint on failure.**
A JSON parse failure or classifier exception must not default to a new wave.
On parse failure: retry once; if still unparseable, log a warning with the task
id and leave `wave_id` unset (null). A null wave_id is acceptable; a fabricated
one is not.

**R4 — Structured output, best-effort with backstop.**
Request structured JSON output from the model where the provider supports it,
but keep robust parsing as a backstop — `response_format` support varies across
litellm providers and is not guaranteed. Both paths must satisfy R3.

**R5 — Dedicated, configurable model.** *(superseded — see fix-wave-classifier-model-config.md)*
Wave classification must use a model configured independently of the coder's
model, defaulting to a fast/cheap-but-capable tier. The default must be capable
enough for semantic matching — do not pick a model so small it degrades match
quality just to save cost.

**R6 — Non-blocking.**
Classification failure must never block or halt task dispatch/execution. Waves
are advisory substrate. Failures are caught, logged with the task id, and leave
the task unwaved; the loop proceeds normally. This replaces the silent swallow.

## Acceptance criteria

- [ ] No wave in a freshly generated `wave.json` has a description or anchor
      field that is a verbatim prefix of a task spec.
- [ ] Two clearly-related task specs (clean descriptions) result in the second
      matching the first's wave — covered by a test.
- [ ] The classification prompt contains no instruction biasing toward "new" on
      uncertainty.
- [ ] A test feeding a malformed/unparseable classifier response asserts
      `wave_id` is null (not "new") and a warning is logged.
- [ ] A test feeding markdown-fenced JSON parses correctly (backstop path).
- [ ] `WaveConfig` exposes a `model` field; its default is not the coder model;
      the classification call uses it. *(superseded by fix-forward)*
- [ ] A simulated `classify_task` exception does not propagate and does not halt
      the loop; a log entry is emitted.
- [ ] Existing engine unit tests pass; new tests cover R2, R3, R4, R6.

## Hints (optional, where genuinely useful)

The fallback to fix in R1 looks roughly like:
```python
feature_description = result.get("new_wave_description", "") or task_spec[:80]
#                                                              ^^^^^^^^^^^^^^^ remove this fallback
```
and the always-garbage anchor:
```python
anchor_summaries = [task_spec[:120]]   # replace with a clean summary, or drop anchors entirely
```

For R4, structured output is typically requested via:
```python
kwargs["response_format"] = {"type": "json_object"}   # best-effort; keep the parser as backstop
```

For R5, `WaveConfig` (`config.py`) currently exposes only `max_age_days` /
`max_idle_days` — add a `model` field and thread it into the classifier call
instead of `self._default_model`.

How to satisfy these contracts otherwise is the implementer's call.

## Notes / deliberately deferred

- Existing `wave.json` files already contain garbage descriptions. This forward
  fix does not clean them; they age out via expiry, and global wave truth is
  owned by the cloud reconciliation layer (ADR-008). Do not attempt a
  retroactive rewrite here.
- Embeddings/vector matching are explicitly deferred — revisit only if
  fragmentation persists after clean input + neutral prompt + fail-safe parsing.
