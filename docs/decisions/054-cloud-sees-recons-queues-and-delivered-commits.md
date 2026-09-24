# ADR 054 — The cloud sees recons, queue-started runs and the commits a merge delivered

## Status

Proposed.

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
- **Processes that never arm liveness.** `cloud_liveness.install()` is
  called only from `snodo run`. Recon and MCP-started runs happen inside
  the MCP server process, which pushes only through the planner's
  status-write hook. The queue runner (ADR 053) would have the same gap.

## Decision

1. **Recon appends history.** `recon_started` (recon_id, query, paths,
   agent count, agent models, session_id, created_at) and
   `recon_completed` (recon_id, the recon's existing final status,
   succeeded and failed agent counts, duration, completed_at). Both are
   declared audit event types with fixed shapes, like every event in the
   ingest union.
2. **Liveness carries recons.** The snapshot gains a `recons` list of
   running recons (id, query excerpt, agent count, started_at). A recon
   whose process is gone is reported stale and dropped, the way stale
   jobs already are, so a recon left `running` on disk does not show as
   live forever.
3. **`task_merged` carries what was delivered.** Added fields: `base_sha`
   (the base branch head before the merge), `commit_count`, `commits`
   (sha and subject, capped with a truncation marker), `files_changed`,
   `insertions`, `deletions`. All are read from git between `base_sha` and
   `merge_sha` at merge time; a failure to measure omits the fields and
   never fails the merge. Commits made outside snodo stay out of scope:
   snodo reports what it merged.
4. **Queue-started runs say so.** Queue state itself is not shipped. The
   `plan_run` event gets a pinned shape (plan_name, job_id, mode, and a
   `trigger` of `mcp`, `cli` or `queue`, plus `queue` naming the queue when
   the trigger is `queue`), and a liveness plan carries `queue` while a
   queue runner is running it. This records who started a run, not which
   queue a plan belongs to.
5. **Every long-lived snodo process arms liveness**: `snodo run`, the MCP
   server and `snodo queue run`. Every push still passes the sync gate,
   so with `cloud.sync_enabled` off nothing goes on the network.
6. **One interface bump.** New event types, new `task_merged` fields and
   the new snapshot sections ship together as cloud interface version 6.
   Older events keep validating as they are; the new fields are optional
   in the shape.
7. **No new state.** Recon statuses are the ones recon already writes;
   queue "stopped" is derived as in ADR 053. No plan or task status,
   severity or halt type is added.

## Consequences

The cloud can wire its Recons counter to `recon_started` and its Commits
counter to `task_merged.commit_count`, and show delivered lines and files
per project, plan and day. History before this change has merges without
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
