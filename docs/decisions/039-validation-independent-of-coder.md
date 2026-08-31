# ADR 039 — Validation is independent of the coder: the engine builds its LLM client from configuration

## Status

Accepted (implemented in `engine/loop.py`; this ADR records it after the fact — #143)

## Context

`GraphBuilder` sourced the LLM client used by the validators and the classifier
off the coder object: `_base_fn = getattr(self.coder, "_completion_fn", None) or
getattr(self.coder, "completion_fn", None)`. Only the `litellm` adapter carries
such a client. For every coder that shells out to a binary — the opencode
adapters (ADR 034) and the external CLI adapters — `_base_fn` was `None`, and
the pre-#143 code set the validator and classifier completion functions to
`None`. Every LLM validator and the classifier then returned "No completion_fn
available", and the run halted at validation before the coder was even
dispatched.

This is an unregistered instance of the failure ADR 035 already names as its
principle: *opting out of a mechanism must not silently discharge the
responsibility that mechanism carried.* ADR 035 enumerated the capabilities the
coder declares to the engine — progress reporting, workspace, correlation ids,
commit switches — and made the engine assign them unconditionally. What it did
not consider was that the engine was quietly depending on one coder attribute
that the ABC never declared, `_completion_fn`, to perform a completely different
job: running the protocol's gates. A coder opting out of an in-process litellm
client silently discharged the responsibility of validation itself.

## Decision

The LLM client that serves the protocol's gates belongs to the engine, not to
the coder. `GraphBuilder.__init__` resolves it as follows:

1. **Use the coder's client when it carries one.** `_base_fn` is still read
   from the coder (`_completion_fn`/`completion_fn`); when present it is the
   base for both clients, so the `litellm` path is behaviour-unchanged.
2. **Build one from configuration when the coder supplies none.** Non-mock runs
   fall back to `litellm.completion`, bound to the resolved model (and to
   `api_base` via `ConfigManager.resolve_api_base`) by
   `_build_completion_fn`. `_base_fn or litellm_completion` — the coder's
   client is preferred, never required.
3. **Mock mode resolves a mock base.** When mock mode is active or the coder is
   `MockAdapter`, the base is the coder's client or `mock_completion_fn` — so
   `--mock` remains hermetic for validators and classifier alike.
4. **Models are resolved per role from snodo config**: `_resolve_model_for_role`
   prefers `llm.validator.model` / `llm.classifier.model` (or the legacy
   `*_llm` key), then the top-level `model`, then the coder's declared model
   (`getattr(self.coder, "model", DEFAULT_MODEL)`). A `model:` override on a
   validator in `protocol.yml` still takes precedence over the validator-role
   default at dispatch.
5. **One client per model.** When the classifier resolves to the same model as
   the validators (non-mock), `classifier_completion_fn` rebinds to the
   validator client — two roles on one model share one bound client.
6. **Fail-closed is unchanged where the client is genuinely absent.** A
   validator with no completion function still returns `severity="blocker"`
   with `error=True` ("No completion_fn available", `llm_validator.py`) and
   halts the run. #143 removed the *engine-side reason* to ever construct a
   `None` client; it did not soften what a `None` client means if one is
   hand-built or the config is unusable.

## Consequences

- The protocol's gates run under every registered coder, including subprocess
  and external-CLI adapters, without the coder exposing any LLM client. The
  conformance suite exercises this across the whole `CODER_REGISTRY` — see the
  amendment to ADR 035 point 2.
- The validator/classifier client is an engine capability resolved from
  configuration; a future coder backend inherits working gates by default.
  Nothing in the coder ABC declares or needs to declare `_completion_fn`.
- The coder's declared `model` remains only a fallback for role-model
  resolution; which model judges is configuration, not coder identity.
- Tests pinning the contract: `tests/engine/test_coder_completion_seam.py`
  (config-built client for a coder without one; litellm path unchanged;
  per-validator override wins; fail-closed on a `None` client).

## Alternatives considered

- **Declare `_completion_fn` on the coder ABC and require every adapter to
  carry one:** rejected — it makes shelling-out adapters fabricate a litellm
  client they have no use for, i.e. it discharges the responsibility by
  mandating the mechanism. Validation is not the coder's job; making the coder
  its carrier re-creates the coupling.
- **Require a `coder: litellm` alongside any LLM-judging protocol:** rejected —
  configuration already resolves providers, models, and api_base; a second
  "judging coder" would be an undeclared second dependency on the same object.
