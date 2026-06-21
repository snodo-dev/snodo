# Spec: inherent task classification — flow_type + emergent waves

## Why
Capture Flow Distribution (feature/defect/debt/risk) and value-grouping
(waves) as inherent, non-circumventable properties of every task — zero
user attrition, uniform org-wide data. "Governance is substrate" applied
to measurement. Feeds snodo-cloud Lead/VP metrics.

## Hook — _governance_node (engine/loop.py:276)
Confirmed convergent point: every task (CLI inline AND MCP/background)
passes through _governance_node as the first graph node, regardless of
protocol config. NOT a validator (those are skippable). One hook covers
both paths.

## One classification call (flow + wave together)
At governance, one cheap LLM call (CF Gemma, no tools, flat cost) reads
task.spec + the wave registry and returns:
  { flow_type: feature|defect|debt|risk,
    wave_id: <existing id> | "new",
    new_wave_description: <summary, only if new> }
Combining is cheaper and lets the model reason about both together.
Bias when uncertain: prefer NEW wave (fragmentation is recoverable via
re-classification; pollution corrupts cycle time irreversibly).

## Wave registry — project-local .snodo/wave.json
Entries: { wave_id, feature_description, anchor_summaries[] (first 3,
LOCKED), created, last_activity, task_ids[] }

Matching context fed to classifier: for each OPEN wave, its
{wave_id, feature_description, anchor_summaries}. anchor_summaries =
the first 3 tasks' summaries, FROZEN once 3 exist (stable fingerprint,
cacheable, not a moving target). Do NOT feed full task history.

Flow:
- read wave.json, filter to OPEN waves (see expiry), feed registry +
  task spec to classifier
- match -> assign wave_id, bump last_activity, append task_id;
  if <3 anchors, append this summary to anchor_summaries (until locked)
- no match -> mint wave_id, set feature_description from this task's
  summary, start anchor_summaries with it, append

## Expiry — wave is OPEN only if BOTH hold
- now - created       < wave.max_age_days   (hard cap)
- now - last_activity  < wave.max_idle_days  (idle close)
Stale wave never matches; next related task mints a fresh wave (same
description + new id = distinct epoch, correct by design).

Config:
  wave:
    max_age_days: 14
    max_idle_days: 5

## Concurrency — same-machine
read->classify->write of wave.json guarded by a file lock. Use the
`filelock` library (MIT), not hand-rolled. Cross-MACHINE divergence is
NOT solved here — it's resolved cloud-side by conflict-triggered
re-classification (separate, snodo-cloud). Local wave.json is
best-effort; cloud re-classification over the merged task set is
authoritative when contributors' assignments conflict.

## Persistence — DO NOT assume LoopState.metadata auto-persists
Extend Task model (core/interfaces.py:36): flow_type, wave_id (Optional).
Write classification results onto the task / LoopState at governance.
CRITICAL: verify they reach the job's state.json that meta reads. The
background path (wrapper.py) writes a LIMITED state.json — if flow_type/
wave_id don't land there automatically, flush them explicitly (same
lesson as the halt-payload fix). Confirm meta/rollup can read them.

## Tests
- _governance_node runs classification for both inline AND background
  dispatch (the convergence requirement)
- flow_type is one of the 4; persisted to task record / state.json
- new feature -> new wave minted, wave.json appended, anchor_summaries
  seeded
- related task within windows -> joins existing wave, last_activity bumped
- anchor_summaries lock at 3, don't change after
- stale wave (age OR idle exceeded) -> new wave even on description match
- concurrent classify on same machine -> file lock prevents double-mint
- flow_type/wave_id readable from state.json by meta

## Touch
engine/loop.py (_governance_node hook), core/interfaces.py (Task fields),
new infrastructure/wave_registry.py (wave.json read/match/append + lock),
infrastructure/config.py (wave block), jobs/wrapper.py (flush flow/wave
to state.json if not auto-persisted), classification prompt/call
(reuse validator LLM path)

Commit: feat(classification): inherent flow_type + emergent wave grouping at governance node
