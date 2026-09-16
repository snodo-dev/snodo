# Spec: `llm.recon.models` as an ordered failover list

## Why

`llm.recon.models` is documented as an ordered model priority list, but
nothing about the resolution was a priority. `resolve_recon_agents` returned
the first `n` entries where `n` is `num_agents`, and each resolved agent was
called once. If the first model returned nothing, the second was never asked —
it was reachable only by raising `num_agents`, which fanned out to both models
in parallel rather than falling back to the second.

Survey was stricter still: it resolved the list and took `agents[0]`, so every
entry past the first was dead on that path regardless of configuration.

Observed on a real project: an operator configured two models specifically
because the first kept disengaging. The first returned an empty result, the
second sat unused in the config, and survey reported that no agent could be
consulted — with a usable model one line below the one that failed.

## What

Make the configured order mean what it says: a model that fails or returns
nothing hands off to the next, and the first that answers wins. Survey honours
the list the same way.

### Lanes

`resolve_recon_agents` now returns one *lane* per agent, and a lane is an
ordered chain of models:

- `num_agents` ≤ 1: one lane holding the whole list, in order. The lane calls
  its models in turn; the first that answers wins. This is the failover path.
- `num_agents` > 1: `n` single-model lanes, run in parallel. This is the
  deliberate fan-out — asking several models the same question to compare
  answers — preserved and unchanged in spirit.
- `explicit_agents`: one single-model lane per name, as before.

`normalize_recon_agents` accepts both the flat `["m1", "m2"]` form (a
single-model lane each) and the nested `[["m1", "m2"]]` form, so callers
written before failover keep working and a state file written before it still
loads.

### Failover semantics

`call_agent_chain` asks its models in order and returns the first result that
is neither an error nor empty. Failover is for **silence and faults**: a raised
call, or an empty result that reads as disengagement.

- A model that answered is never retried — a poor answer is an answer, and
  failover is not for verdicts you dislike.
- Each model is called at most once, so the number of calls is bounded by the
  length of the chain.
- Every attempt is recorded on the result (`attempts: list[ReconAttempt]`) with
  the model and why it was passed over, so an operator can see which models
  were tried and why.

No new state, severity, halt type or task status is introduced.

### Warnings

The warning behaviour was backwards for the case that matters. Now:

- More models configured than `num_agents`: warn once, naming the unused tail
  and how to reach it (`num_agents=1`).
- Fewer models than `num_agents`: warn once that the fan-out is smaller than
  requested, not once per empty slot.
- No models and `num_agents` > 1: warn that duplicates add no value, as before.

All warnings go to stderr; primary output stays on stdout.

## Tests

- a first model returning nothing results in the second being called and its
  answer returned
- a first model answering means the second is never called
- a poor answer is not retried
- every model failing reports which were tried and why
- survey reaches the second entry the same way
- explicit fan-out still calls every requested agent
