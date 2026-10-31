# ADR 060 — Operator intent is a first-class record

## Status

Accepted. Fixes #1042.

## Context

Plans explain what work will be done, but the operator's reason for asking for
that work should remain independently traceable. If intent exists only as text
inside a plan, later plans cannot reliably cite the original reasoning, and
corrections risk erasing the historical account. ADR 038 establishes that
orchestration-time artifacts belong in the append-only audit trail. ADR 054
describes plan history events and the cloud contract; ADR 055 records the
proposed client-side coordination direction for orchestrators.

## Decision

Capture operator intent as its own record before planning. A plan may carry an
optional `intent_ref` naming one intent record. The record may contain one
optional GitHub issue reference.

An intent record is written once and is never edited in place. To correct or
revise an intent, write a new record that supersedes the old one: fix forward
without rewriting history. There is no intent lifecycle status. Whether an
intent has been acted on is read from the plans that point to it, not from a
status field on the intent.

Add one local-only audit event, `intent_recorded`, for recording the intent.
It is not part of the cloud contract. `intent_ref` does not ride on the
`plan_proposed` or `plan_run` history events; the cloud wire and interface
version therefore remain unchanged.

This adds no state, status, severity or halt type.

## Consequences

Plans can trace back to the operator's durable rationale, and later tasks can
cite that rationale without depending on mutable plan text. Corrections remain
visible as new records, while plan references show whether and where an intent
was acted on. The audit trail gains a local record of intent without expanding
the cloud event contract or requiring a cloud interface version change.

## Alternatives

A mutable intent with a status field was rejected: it introduces another
lifecycle state and makes the record's history less durable. Carrying
`intent_ref` on `plan_proposed` or `plan_run` was rejected because it changes
the cloud contract and would require a corresponding interface change.
