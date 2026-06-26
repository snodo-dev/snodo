# FIX-FORWARD: wave classifier live call

## Goal
The classifier errors on every live call and fails silent, so no waves form
(tests pass because they mock the call). Make the real call succeed.

## Scope
`wave_registry.py`, `loop.py`. Engine only.

## Contracts
C1 — Do not send `response_format`. Providers reject it; the JSON parser
backstop (direct / fenced / bare-`{}`) is the guarantee. Remove the
`supports_response_format` block entirely.
C2 — Resolve `api_base` for the classifier the same way the coder does, so
custom-endpoint providers (Cloudflare, OpenRouter) route correctly. Today the
classifier sets neither api_base nor provider headers; mirror the coder.
C3 — A total classifier failure (after the one retry) must surface visibly on
stderr — currently only a logger.warning fires, which is invisible in prod, and
the outer handler in loop.py is dead code because nothing propagates. Make the
failure observable while staying non-blocking (task left unwaved, dispatch
proceeds).
C4 — Parser backstop unchanged.

## Acceptance
- No `response_format` anywhere in the classifier path.
- Classifier resolves api_base identically to the coder.
- A forced classifier failure prints visible stderr context and does not crash
  the run; task is left unwaved.
- Smoke: with the real configured provider, classification succeeds and two
  related dispatches share a wave.

## Notes
The litellm exception banner on every run is the symptom of C1; it should stop
once response_format is gone.
