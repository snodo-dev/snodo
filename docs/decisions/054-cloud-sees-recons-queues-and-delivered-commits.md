# ADR 054 — The cloud sees recons, the plan hierarchy and the commits a merge delivered

## Status

Accepted (2026-09-25), with the amendments below.

## Context

The cloud's view is built from two streams: the audit log (history, synced
by `cloud_sync`) and liveness snapshots (what is running now, pushed by
`cloud_liveness`). Three things the operator cares most about are missing
from both.

- **Recons.** A recon writes `.snodo/recons/<id>/state.json` and
  `results.json` and appends no audit event. The snapshot reads only
  `.snodo/plans`, `.snodo/tasks` and `.snodo/jobs`. Projects hold dozens of
  recon runs; the cloud's Recons counter has no source.
- **Delivered work.** `task_merged` is emitted and synced with `task_ref`,
  `branch`, `merge_sha`, `spec` and `session_id`, but carries nothing about
  what the merge delivered. Projects merge tens of commits a day through
  snodo; the cloud can count merges but cannot show commits, files or
  lines.
- **The plan hierarchy.** No task event carries its plan: `dispatch`,
  `task_complete`, `task_merged` and `halt` carry `task_ref` and
  `session_id` only. The `wave_id` on `task_classified` is the wave
  classifier's own id (`w_002a`), not the plan's wave. `plan_run` and
  `plan_proposed` are opaque in the ingest union, and only the MCP path
  emits `plan_run`; `snodo plan run` emits neither. Without a live snapshot
  the cloud cannot rebuild plan → wave → task, and inferring it from branch
  names or `task_ref` prefixes is a convention, not a contract.
- **Processes that never arm liveness.** `cloud_liveness.install()` is
  called only from `snodo run`. Recon and MCP-started runs happen inside
  the MCP server process, which pushes only through the planner's
  status-write hook. The queue runner (ADR 053) would have the same gap.

## Decision

1. **Recon appends history.** `recon_started` (recon_id, query, paths,
   agent count, agent models, session_id, created_at) and
   `recon_completed` (recon_id, the recon's existing final status,
   succeeded and failed agent counts, duration, completed_at, and a
   summary of the answer capped at about 2,000 characters with a
   truncation marker). `recon_started` carries the question in full. Both are
   declared audit event types with fixed shapes, like every event in the
   ingest union.
2. **Liveness carries recons.** The snapshot gains a `recons` list of
   running recons (id, query excerpt, agent count, started_at). A recon
   whose process is gone is reported stale and dropped, the way stale
   jobs already are, so a recon left `running` on disk does not show as
   live forever.
3. **`task_merged` carries what was delivered.** Added fields: `base_sha`
   (the base branch head before the merge), `commit_count`, `commits`
   (sha and subject, capped at 50 with a truncation marker; `commit_count` is always the full count), `files_changed`,
   `insertions`, `deletions`. All are read from git between `base_sha` and
   `merge_sha` at merge time; a failure to measure omits the fields and
   never fails the merge. Commits made outside snodo stay out of scope:
   snodo reports what it merged.
4. **History carries the plan hierarchy.** `task_classified`, `dispatch`,
   `task_complete`, `task_merged` and `halt` gain `plan_name` and
   `plan_wave` (the plan's wave id, distinct from the classifier's
   `wave_id`) when the task belongs to a plan. `plan_proposed` and
   `plan_run` get pinned shapes, `{plan_name, waves: [{wave_id, task_refs}]}`,
   and both the CLI and the MCP path emit them. `plan_run` also carries
   `trigger` (`mcp`, `cli` or `queue`) and, when the trigger is `queue`, the
   `queue` that started it, plus `job_id` (the background job running the
   plan, or null for a foreground run) and `mode` (the protocol mode at run
   start). Queue state itself is not shipped: the record is
   who started a run, not which queue a plan belongs to. A liveness plan
   carries `queue` while a queue runner is running it.
5. **Every long-lived snodo process arms liveness**: `snodo run`, the MCP
   server and `snodo queue run`. Every push still passes the sync gate,
   so with `cloud.sync_enabled` off nothing goes on the network.
6. **One interface bump.** New event types, new `task_merged` fields and
   the new snapshot sections ship together as cloud interface version 6.
   Older events keep validating as they are; the new fields are optional
   in the shape. The lease-mint response advertises `interface_version`; a
   missing or invalid value means version 5. The client sends v6 only when
   the current lease advertises 6 or later. Under v5, it sends unchanged
   v5-compatible events and holds at the first v6-only event or v6-added data
   field, including the remaining chain suffix. It does not strip fields,
   since that would make `event_hash` attest to different data. A held event
   is retried with a newly minted lease on a later sync. Sync is not refused,
   no event is lost, and the hash chain the cloud receives has no gap. The
   new events are always written to the local audit log; only what goes on
   the wire waits. snodo-cloud must include the currently accepted
   `interface_version` in its successful lease-mint response and change it
   to 6 only after ingest and liveness accept v6.
7. **No new state.** Recon statuses are the ones recon already writes;
   queue "stopped" is derived as in ADR 053. No plan or task status,
   severity or halt type is added.

## Consequences

The cloud can wire its Recons counter to `recon_started` and its Commits
counter to `task_merged.commit_count`, show delivered lines and files per
project, plan and day, and rebuild plan → wave → task for any window from
history alone. History before this change has merges without
stats; they count as merges with unknown size. snodo-cloud must accept
interface version 6 before a client sends it; until then a client sending
version 6 events would be refused, so the cloud side ships first or
together.

## Alternatives

Having the cloud read commits from the git host was rejected: it needs
repository access the cloud does not have and would count commits snodo
did not make. Deriving recon history from liveness alone was rejected:
liveness is a view of now, and a recon that finishes between pushes would
never be counted. Shipping recon state files through sync was rejected in
favour of declared events, which keep the hash chain and the closed ingest
union.
