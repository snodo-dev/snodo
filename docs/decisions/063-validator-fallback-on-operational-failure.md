# ADR 063 — Validator fallback only on operational failure

## Status

Accepted. Fixes #1290.

## Context

Validator model fallbacks provide an ordered route to another model when the
configured model cannot complete validation for operational reasons. Validation
already distinguishes a validator that produced no verdict from one that
submitted a verdict: the former is `validator_error` ([015](015-mcp-validation-four-outcome-contract.md)), while the latter is final, including a verdict that is unusable ([059](059-verdict-names-its-task.md)). The `llm` configuration section rejects unknown keys rather than silently accepting them ([046](046-unknown-llm-config-key-fails-loud.md)).

Without an explicit boundary, fallback could be mistaken for a way to obtain a
more favourable or more useful judgement, changing the meaning of validation.

## Decision

The validator model setting may be a short ordered list. After the existing
retries for a model are exhausted, the next model is tried only if the preceding
attempt failed operationally and produced no verdict. A submitted verdict of
any severity is final; an unusable verdict is also final. Neither may trigger a
model switch.

The classifier uses the same ordered list and switches only after operational
failure. Each model switch is recorded as one job-log line. If every configured
model is exhausted without a verdict, validation returns the existing
`validator_error` outcome; this decision introduces no new state, status,
severity, or halt type.

## Consequences

Operators can configure ordered alternatives for operational resilience without
changing verdict semantics. Job logs show each transition, and exhaustion
continues through the existing validator-error handling.

## Alternatives

Switching after a disagreeing or low-quality verdict was rejected. A second
opinion chosen after seeing the first is shopping for a verdict: it makes model
selection depend on the answer and undermines the finality of a submitted
verdict. Retrying after an unusable verdict was also rejected; that remains the
existing validator-error case, not permission to ask another model.
