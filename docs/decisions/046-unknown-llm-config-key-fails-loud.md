# ADR 046 — An unknown `llm` config key is rejected, not silently dropped

## Status

Accepted.

## Context

`infrastructure/config.py` declares the `llm` section as pydantic models that
left the default extra handling in place. A key that was not a field was
discarded in silence: the file parsed, nothing warned, and the value had no
effect. A typo (`temprature`) and a knob that was never implemented looked
exactly alike, and the only way to learn a setting was inert was to watch it do
nothing.

Observed on a real project: a validator section carried `single_max_tokens` and
`temperature` alongside the three real fields. Neither exists on
`ValidatorConfig` — `docs/specs/llm-config-section.md` states that v1 scope is
exactly four knobs and that `temperature` and `single_max_tokens` are not moved
— and both were dropped without a word. The operator believed a budget and a
temperature were configured for weeks.

The `llm` section is owned end to end by the engine: no other tool writes it as
a pass-through, and every key in it is read through a typed field. There is no
forward-compatibility contract that an unknown key serves. The choice is
therefore between a warning and a hard rejection; silence is not defensible in
either case.

## Decision

**An unknown key under `llm` is a `ConfigLoadError`.** Every model in the
section (`LlmConfig`, `CoderConfig`, `ValidatorConfig`, `ValidatorLLMConfig`,
`ClassifierConfig`, `ReconConfig`, `WaveConfig`) sets
`model_config = ConfigDict(extra="forbid")`. When validation fails only on
extra keys, `load_llm_config` raises with a message naming each key and the
section that owns it, and listing the section's valid keys. A non-extra
validation failure keeps the existing generic message.

**The migration runs before validation.** `_migrate_wave_classifier_keys` pops
the deprecated `llm.wave.max_tokens` / `temperature` and moves them to
`llm.classifier` before `LlmConfig` is constructed, so a supported migration
keeps its values and its `DeprecationWarning` and is never mistaken for an
unknown key.

**No new behaviour is introduced beyond the rejection.** No default changed, no
field was added, renamed or implemented, and no state, severity, halt type or
task status was added.

## Consequences

- A config that sets only real keys loads exactly as before, silently.
- A config with a mistyped or unimplemented `llm` key no longer reaches a run;
  the operator is told the key and the section and can fix it.
- A `ConfigLoadError` from this rejection is handled the way config errors
  already are: `build_protocol_graph` propagates it to the CLI, the validator
  hot path turns it into a `blocker` (W1.5-04). A malformed `llm` key cannot
  become a silent default.
- The rejection applies only to the `llm` section; other top-level sections of
  `config.yml` are read as raw dicts by `ConfigManager` and are untouched.

## Alternatives

- **Warn and continue** (drop the key but print a notice): rejected. The `llm`
  section is fully engine-owned and has no forward-compatibility contract to
  protect; a warning still lets a typo reach a run with the wrong behaviour,
  and it is one more line the operator learns to scroll past. The strict
  option is affordable here precisely because there is nothing to be
  compatible with.
- **Keep pydantic's default (ignore)**: rejected — this is the defect. A
  setting that parses, does nothing and says nothing is indistinguishable from
  one that works.
- **Fail on unknown keys everywhere in `config.yml`**: rejected as out of
  scope. Only the `llm` section is typed and engine-owned; the rest is raw
  dict storage that other paths read opportunistically.
