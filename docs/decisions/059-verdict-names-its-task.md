# ADR 059 — A submitted verdict names the task it judged

## Status

Accepted. Extends [045](045-a-judge-is-made-to-decide.md). Fixes #951.

## Context

A pre-execute judge passed a proposal while explaining a decision record for
another project's interface. Nothing in its answer identified the subject.
Confident prose is not evidence that a verdict applies to the current task.

## Decision

Every judge answering through `submit_verdict` receives the task identifier
and must return it in the required string argument `task_id`, alongside
`severity` and `justification`. The live tool boundary compares it exactly
with `context.task.id`, in both pre-execute and post-execute evaluation,
including acceptance judges and plugin-tool judges. No trimming, case folding,
identifier coercion, or prose relevance scoring is performed.

An omitted or different identifier is an unusable verdict. Return the existing
`error=True`, `severity="blocker"` result, naming the judge and the expected and
received subject (or its omission) in the justification. The existing run
payload and validation audit records carry that reason, and policy halts as
`validator_error`. It is not a warning, a criterion finding, a tool-access
refusal, or input for coder recovery or spec-authoring retries. Do not apply
`tool_access_missing` from an answer rejected for its subject: one shortfall
must not be handled twice.

The check terminates the judge call rather than soliciting a replacement
verdict. An exact match preserves the previous severity, justification and
tool-access handling. Criteria, phases, tool grants, caps and quorum policy
are unchanged. Single-completion JSON judges are outside this tool contract.
Cache keys and stored records remain unchanged; cache provenance is separate
work and this decision applies to newly submitted verdicts, not cache hits.

## Consequences

A favourable answer about an unnamed or different task cannot approve coder
dispatch. Operators see the reason on the same error surfaces as other
unusable verdicts. Existing deterministic judge fixtures must echo the task
identifier just as a real judge must.

## Alternatives

Prose similarity scoring was rejected: it is nondeterministic and changes the
meaning of judgement. A new severity or retry stage was rejected: the existing
validator-error contract already fails closed without charging unrelated work.
