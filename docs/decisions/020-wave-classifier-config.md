# ADR 020 — Wave classification reads `ClassifierConfig`; the classifier model is resolved once

## Status
Accepted

## Context
`infrastructure/config.py` defined both `ClassifierConfig` and `WaveConfig`, and
both carried `max_tokens` and `temperature` (defaulting to 500 / 0.0). The wave
classifier's LLM call (`WaveRegistry._call_classifier`) read `WaveConfig`'s
copies, so `llm.classifier.max_tokens` and `llm.classifier.temperature` were
inert — a user raising the budget under `llm.classifier` saw no effect, silently.
The spec that introduced `ClassifierConfig`
(`docs/specs/fix-wave-classifier-model-config.md`, C3) said `llm.classifier`
should carry model, max_tokens and temperature and the classification call
should use them; the implementation wired `WaveConfig` instead. Accidental
duplication, not design.

The classifier model was also resolved twice by different paths. The completion
function was bound (model + api_base) via `_resolve_model_for_role` from the raw
config dict; the call then passed a `model` kwarg resolved separately
(`llm_cfg.classifier.model or self._default_model`), which overrides the bound
model but not the bound api_base. When the two disagreed, the call went to the
right model at the wrong endpoint. `governance.py:_classify_wave` duplicated the
classification block inlined in `loop.py:_governance_node`.

## Decision

1. **`WaveConfig` keeps only the wave-lifetime fields** (`max_age_days`,
   `max_idle_days`). Classification budget and temperature come from
   `ClassifierConfig`, which `WaveRegistry` now receives as a separate
   `classifier` argument and reads in `_call_classifier`.

2. **The classifier model is resolved exactly once**, in
   `GraphBuilder.__init__`, and stored as `self._classifier_model`. The same
   value binds the completion function (model + api_base) and is passed to the
   classification call, so model and api_base can never disagree.

3. **The duplicated classification path is collapsed.** `loop.py`'s
   `_governance_node` now calls the single `_classify_wave` method (in
   `GovernanceNodeMixin`) instead of inlining the block.

4. **Migration, not silent reversion.** `llm.wave.max_tokens` / `temperature`
   were the only working classifier knobs, so dropping them would silently
   revert a user's raised budget to the default. `load_llm_config` migrates them
   to `llm.classifier` (classifier wins if both are set) and emits a
   `DeprecationWarning` plus a one-time stderr notice naming the new keys.

5. **`snodo config set` accepts `classifier.*` and the remaining `wave.*`
   keys** (`classifier.model`, `classifier.max_tokens`, `classifier.temperature`,
   `wave.max_age_days`, `wave.max_idle_days`). The deprecated
   `wave.max_tokens` / `wave.temperature` are rejected with a message pointing
   at the new key name.

## Consequences

- `llm.classifier.max_tokens` / `temperature` now actually reach the
  classification call.
- A config that sets only the old `llm.wave` keys keeps working: the values are
  migrated, not dropped.
- The classifier model fallback changes subtly: it now falls back to the
  top-level `model` (via `_resolve_model_for_role`) rather than the coder's
  model, matching the spec's C1 and making the completion function and the call
  agree.
- `WaveConfig` no longer exposes `max_tokens` / `temperature`; any code reading
  them must read `ClassifierConfig` instead.

## Alternatives considered

- **Fail loudly on the old wave keys** (raise `ConfigLoadError`): rejected —
  the old keys were the only working knobs, so failing would break a working
  config. Migration preserves behaviour while telling the user to move the key.
- **Silently drop the old wave keys**: rejected — reverts a user's raised
  budget to the default with no signal.
- **Keep the two config classes as-is and just fix the call site**: rejected —
  leaves a `wave` section carrying classifier tuning, which is the lie that
  caused the divergence.
