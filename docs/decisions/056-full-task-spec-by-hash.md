# ADR 056 — Full task specs in the cloud live view by content hash

## Status

Accepted (2026-10-04), option A.

## Context

The cloud live view can show a task's status but not its full spec. The
deliberate emit contract in `snodo/cloud_liveness.py` (`_collect_runs`) sends
only the task id, never the spec. This keeps specs and prompts off the liveness
wire, but a live task rendered without its task text reads as broken. Specs
reach the cloud today only through synced history, which is not a source for a
task's current live view. Plan intents have the same need.

## Decision

When cloud sync is enabled and the current lease advertises interface version 9
or later, the client may upload task specs to the cloud live view under this
scoped exception to the emit contract:

1. On the first liveness snapshot in which a task appears, whatever its state,
   upload its exact spec as raw UTF-8 bytes to
   `PUT /v1/live/specs/{sha256}`, where `{sha256}` is the SHA-256 digest of
   those exact bytes. The request uses the API-key bearer credential. Plan
   intents use the same endpoint and content-addressing rule.
2. Upload a spec only once per hash, tracked by a local cache of successfully
   uploaded hashes. A successful 2xx response and an already-present response
   both count as complete. Rate limiting honors `retry_after`; other upload
   failures are best-effort and never block the liveness snapshot.
3. Specs longer than 20,000 characters are not uploaded and their liveness
   entries carry no `spec_hash`. Otherwise, each liveness task entry carries
   `spec_hash` alongside its existing fields, identifying the uploaded copy.
4. Send this behavior only when cloud sync is enabled and the lease advertises
   interface version 9 or higher. If either gate is unmet, liveness does not
   upload specs or carry `spec_hash`.

Synced history remains authoritative. The hash establishes that the uploaded
copy is the exact content represented by that digest; it does not replace
history or make liveness a historical record. This is the client-side decision
paired with [snodo-cloud ADR-0040](https://github.com/snodo-dev/snodo-cloud).

## Consequences

With cloud sync enabled and interface v9+, task specs and plan intents leave the
machine on the liveness channel, enabling the cloud's live view to render the
full task context. Content addressing permits deduplication and lets the cloud
verify the uploaded bytes against the liveness reference. History remains the
record of authority. Failed uploads can leave a snapshot without a resolvable
spec, but cannot stall live status reporting. Mock and benchmark runs never
upload specs.

## Alternatives

Keep specs off liveness and rely only on synced history; rejected because the
live view can show a task without the text that explains it. Send the spec in
every snapshot; rejected in favour of a bounded, content-addressed upload once
per hash.
