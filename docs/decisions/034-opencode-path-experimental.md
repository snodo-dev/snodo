# ADR 034 — The opencode coder path is experimental, not supported

## Status

Accepted

## Context

The coder seam analysis (`docs/architecture/coder-adapter-contract.md`) called
for a stated position on whether the opencode path is supported or
experimental. The evidence pointed at experimental:

- **Missing progress reporting.** The engine gained per-turn progress
  reporting behind a `hasattr` guard; the opencode adapters do not define the
  attribute, so the feature silently does not exist on them.
- **Missing usage/cost tracking.** The opencode adapters never touch litellm,
  so an opencode run's spend is entirely absent from the audit trail.
- **Untouched by three consecutive adapter-facing changes.** The opencode
  adapters' tests were not updated by #39, #51 or #53, while `litellm.py` was
  modified four times in two days.
- **The cost of sitting between the two.** The container adapter silently
  blinded post-execute reviewers for two months because nobody had decided
  whether the path was held to the same standard as the supported one.

## Decision

The opencode coder backends — `opencode` (containerised, Docker/HTTP) and
`opencode-cli` (host `opencode run`) — are **experimental**, not supported.

"Experimental" means: the path is exercised by the conformance suite
(`tests/coders/test_adapter_conformance.py`) with real protocol validators running
against engine-level completion function resolution, and the structural guarantees
hold for it — the `.snodo/` mutation guard and the commit that keeps the
review channel correct are enforced by the `InPlaceCoderAdapter` base class
(ADR 027/030), so it cannot silently blind reviewers again. But it is not a
production default: it does not yet report per-turn progress or contribute
usage/cost records, and no shipped protocol template uses it.

### Cost attribution is operational telemetry, not part of the attestation

Cost is **not** part of what snodo attests to. The audit trail (INV4, ADR 031)
records what a run decided and whether its verification commands ran — it never
carries cost, for any coder. Token and cost data live in per-job `state.json`
(`UsageTracker` → `snodo meta`), which is operational telemetry, not an
attestation.

Two consequences follow:

1. The opencode paths' absence of usage/cost records is a **non-goal**, not a
   gap in the attestation. The experimental designation already states it; this
   ADR makes the reason explicit.
2. The supported `litellm` path does not get a free pass either — it reports
   usage only for background jobs (`job_id != "unknown"`) and never into the
   audit log. If cost ever becomes part of the attestation, it is a decision
   about the attestation format for **all** coders, not a litellm-vs-opencode
   parity question. That decision is not made here.

The position is recorded where an operator meets the path:

- `docs/protocol.md` — the `coder` field and a "Coder backends" section.
- `docs/architecture.md` — the adapter-pattern section.
- `snodo init` output — the Docker check now prints the experimental note.
- `docs/runbooks/01-minimal-webapp.md` — the sample init output.

## Consequences

- An operator who sees the opencode path in `init` output or a protocol is
  told it is experimental and what that means, instead of inferring support.
- The absence of progress reporting and usage/cost records on the opencode
  path is now a stated decision, not an oversight: cost is operational
  telemetry (`snodo meta`), not part of the audit trail's attestation.
- Promoting the path to supported is a concrete checklist: add per-turn
  progress, add usage/cost records to the same telemetry the `litellm` path
  uses, and adopt it in a shipped template. Cost into the audit trail is a
  separate, all-coder decision and is not part of this checklist.

## Alternatives considered

- **Supported:** rejected — the missing progress and usage/cost records and
  the untested drift are exactly the conditions that produced the two-month
  reviewer-blindness; declaring support would repeat it.
- **Unstated (status quo):** rejected — sitting between supported and
  experimental is how the container adapter came to blind the reviewers.
- **Cost as part of the attestation:** rejected for this decision — the audit
  trail records decisions and verification evidence, never cost, for any
  coder; making cost attestable is a change to the attestation contract
  itself, deliberately out of scope here (issue #69).
