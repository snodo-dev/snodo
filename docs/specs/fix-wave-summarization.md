# Spec: wave classifier must LLM-summarize, not slice the spec

## Problem (recon-confirmed, two agents)
wave.json degenerated to ~1:1 wave:task — 19 waves for 19 tasks,
clearly-related work (8+ svelte-migrate-* dashboard tasks) split into
separate waves instead of clustering. Root cause, all in
infrastructure/wave_registry.py:

- feature_description = task_spec[:80] (wave_registry.py:105) — a raw
  byte slice, NOT a summary. Falls back to slice whenever the LLM omits
  new_wave_description, which is OFTEN because the prompt lists it as
  optional JSON.
- anchor_summaries = task_spec[:120] (lines 109, 168) — ALWAYS a raw
  slice, never an LLM summary. The field is named "summary" but is a
  substring.
- Because the matcher compares an incoming task against these garbage
  spec-dumps, it can never find semantic overlap -> mints new every
  time. The "if uncertain return new" bias (line 204) makes every
  uncertain case a fresh wave.

The LLM is used to CLASSIFY but never to SUMMARIZE. That missing
summarization is the whole bug.

## Required behavior — the mechanism is the point, do not slice
The single classification LLM call must return, as REQUIRED fields
(not optional), for every task:
  - flow_type: feature|defect|debt|risk
  - wave_id: an existing wave_id OR "new"
  - task_summary: a clean one-line summary of THIS task (e.g.
    "Migrate Team page to SvelteKit"), NOT a copy/prefix of the spec
  - feature_description: ONLY when wave_id == "new" — a short FEATURE
    LABEL describing the body of work (e.g. "SvelteKit dashboard
    migration"), NOT a copy/prefix of the spec, broader than a single
    task so sibling tasks match it

HARD CONSTRAINT: feature_description and anchor_summaries/task_summary
must NEVER be assigned from task_spec[:N] or any substring of the spec.
If the LLM fails to return a required field, retry the call; if it
still fails, that is an error to surface — do NOT silently fall back to
a spec slice. (A spec slice is what caused this bug and must be
impossible by construction.)

## Changes
1. Prompt (wave_registry._build_prompt ~172-210): make task_summary
   and feature_description REQUIRED in the JSON schema. Instruct
   explicitly: "feature_description is a short feature label for the
   body of work, broader than one task, never a copy of the spec.
   task_summary is one line describing this task, never a copy of the
   spec." Give a good/bad example in the prompt
   (good: "SvelteKit dashboard migration";
    bad: "VALIDATION TOKEN: svelte-migrate-team CONTEXT: ...").

2. Mint path (~105-113): feature_description = the LLM
   feature_description (required). anchor_summaries seeded with the LLM
   task_summary. NEVER task_spec[:80]/[:120].

3. Assign path (~168): the appended anchor summary (while < 3, first-
   three-locked logic unchanged) = the LLM task_summary, NEVER
   task_spec[:120].

4. Matching bias (~204): soften. Replace "if uncertain return new"
   with: "Assign to an existing wave when the task is part of the same
   feature or effort as that wave. Only return 'new' if this is
   genuinely a different feature." A mild lean to new for true
   ambiguity is fine; do NOT instruct minting on any uncertainty —
   with real summaries the matcher should usually find the right wave.

5. No-slice guard: remove every `task_spec[:N]` assignment into
   feature_description / anchor_summaries / task_summary. If a required
   LLM field is missing after retry, raise/log — never slice.

## Acceptance — verify on REAL dispatch, not unit tests
Unit tests here verify STRUCTURE and have repeatedly passed while the
behavior was broken. The acceptance test is semantic:
- wipe .snodo/wave.json, dispatch several RELATED tasks (e.g. 3-4
  "migrate <page> to SvelteKit" tasks)
- they CLUSTER into ONE wave whose feature_description is a clean
  feature label (e.g. "SvelteKit dashboard migration"), NOT a spec
  prefix
- an UNRELATED task (e.g. a backend bugfix) mints a separate wave
- cat .snodo/wave.json: NO feature_description or anchor_summary is a
  "VALIDATION TOKEN: ... CONTEXT: ..." spec dump
Also add a unit test asserting feature_description/anchor entries do
NOT start with the spec's leading text (i.e. are not raw slices) — so
this regression is caught structurally next time.

## Touch
infrastructure/wave_registry.py only

Commit: fix(waves): require LLM feature_description + task summaries (never spec slices), soften new-wave bias
