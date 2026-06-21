# FIX-FORWARD: wave classifier model selection via `llm.classifier`

## Goal

The shipped wave classifier hardcodes `gemini/gemini-2.0-flash` in `WaveConfig`.
That model was retired on 2026-06-01, so the classifier now errors on every
call — it fails safe (no wave fabricated, logged) but waves never form.

Move classification-model selection into a dedicated `llm.classifier` config
section that falls back to the top-level default `model`. No model identifier
should appear in engine source — model retirement is a config concern, not a
code change.

This is a fix-forward on the already-shipped wave classifier work (clean
descriptions, neutral bias, fail-safe parsing, non-blocking dispatch). Touch
ONLY the model-wiring. Do not re-open the shipped behavior.

## Scope

Python OSS engine only:
- `config.py` — remove the hardcoded `WaveConfig.model`; add a `classifier`
  section under `llm`.
- `engine/loop.py` / `wave_registry.py` — resolve the classifier model from
  config with fallback; pass it to the classification call.

Out of scope: the shipped R1–R4/R6 behavior; expiry semantics; the combined
flow_type+wave single-call design; cloud workers; any retroactive cleanup of
existing `wave.json`.

## Contracts

**C1 — Config-driven, with fallback.**
The classification model is read from `llm.classifier.model`. When that field
is unset, or the `classifier` section is absent, it falls back to the top-level
default `model`. The fallback must always resolve to a valid model.

**C2 — No model ID in source.**
No model identifier string for classification appears anywhere in engine source.
Grepping the source for a model name returns nothing.

**C3 — Section shape consistent with siblings.**
`llm.classifier` follows the same convention as `llm.validator` — at minimum
`model` (optional), `max_tokens`, `temperature` — and the classification call
uses these values.

**C4 — Structured output stays best-effort, backstop is the guarantee.**
`response_format` remains best-effort only. The configured model may be a
non-OpenAI endpoint (Workers AI Gemma, DeepSeek) that does not honor it, so the
fence/bare-`{}` parser backstop is what actually guarantees parsing. Do not
remove the backstop on the assumption response_format covers it.

## Acceptance criteria

- [ ] With `llm.classifier.model` set, the classification call uses that model.
- [ ] With the section omitted or `model` unset, the call uses the top-level
      `model` — covered by a test asserting both resolution paths.
- [ ] No hardcoded classification model identifier remains in engine source.
- [ ] Existing wave-classifier tests still pass; ruff clean.
- [ ] (Smoke, not unit) With `classifier` configured to a cheap model, two
      related dispatches form a shared wave.

## Hints (config shape + resolution; how is otherwise yours)

```yaml
llm:
  classifier:
    model: openai/@cf/google/gemma-4-26b-a4b-it   # omit -> top-level `model`
    max_tokens: 500
    temperature: 0.0
model: deepseek/deepseek-v4-flash                  # default fallback
```

```python
classifier_cfg = getattr(cfg.llm, "classifier", None)
model = (classifier_cfg.model if classifier_cfg else None) or cfg.model
```

## Notes / deferred

Existing `wave.json` files keep their old descriptions; they age out via expiry,
and global wave truth is owned by the cloud reconciliation layer (ADR-008). No
retroactive rewrite here.
