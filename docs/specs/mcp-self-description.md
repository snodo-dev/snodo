# Spec: MCP self-description (instructions + resources)

## Current state (0.18)

The original design below records the motivation and proposed shape; its original
workflow sketch and acceptance criteria are historical where they differ from
the shipped surface. The 0.18 session instructions direct each orchestrator to
call the read-only `guide` first and list the guide topics available to that
server's exposed tools. The guide is sourced from the topic markers in the docs;
the current topics include `waves`, `planning`, `spec`, `mistakes`,
`following-a-run`, `queues`, and `queue-triage` where relevant to the mode.

For background work started by `dispatch_task`, `run_plan`, or `queue_run`, the
instructions say to call `watch_job(job_id)`. The text snapshot is available
without an MCP Apps host; suggest the configured cloud live view for task
status/progress when available and always suggest `snodo logs <job_id> --watch`
for live output. Do not hand out the MCP server's `/watch/` capability URL. Use
`get_job_status` or `get_job_logs` for a specific follow-up, not as the routine
polling loop. See [Following a run](../following-a-run.md).

Instructions recommend the smallest structure that fits: one direct task, a
wave for parallel tasks, a plan for multiple waves, and a queue for several
plans. Tool access is bounded by the active mode's capability grant. A server
without an explicit `--mode` serves the current mode. The implementation's
current tool resolution for that case differs; see the report for the code/doc
discrepancy.

## Original design proposal (historical)

## Why

A remote/SSE orchestrator has NO filesystem — it can't read protocol.yml, tail audit.log,
or ls sessions. Today the server sets no `instructions` and exposes no resources
(recon: transport.py:56 calls FastMCP(server_name) with nothing else; zero @resource).
So a fresh orchestrator either reverse-engineers the workflow (stdio, by luck) or is
blind (remote). This both fixes the "nudge it every new chat" problem and is mandatory
groundwork for the remote/SaaS direction.

FastMCP 1.27.1 supports: `instructions` in the init handshake, `@resource` URI-addressable
data, `@prompt`. SSE transport already works. No transport changes needed.

## Part 1 — Server `instructions` (transport.py:56)

Pass an `instructions=` string to FastMCP(...), built from the loaded protocol at server
construction. It lands in the initialize handshake — present in every session, before any
tool call. It is the canonical operating manual. Required sections:

- **What this is**: a protocol engine governing AI-SDLC. The orchestrator coordinates via
  tools only; it has no direct filesystem access — all knowledge comes through tools and
  resources.
- **Role model**: orchestrator coordinates and never writes files; the coder writes;
  validators are read-only. Mutations are token-gated (WF1).
- **The workflow loop (per task)**, explicit and ordered:
    validate_task(task_id) -> validator results + token
    dispatch_task(task_spec) -> job_id
    get_job_status(job_id) -> poll until completed | failed
    get_job_logs(job_id, tail=N) -> read output, especially on failed
- **The async contract — state this loudly, it is the #1 failure mode**:
    dispatch_task is ASYNCHRONOUS. It returns a job_id and returns immediately; the coder
    runs in the background. A pre-execute validation pass does NOT mean the task succeeded.
    Only a job whose status is `completed` (exit 0) with the file written confirms success.
    Poll get_job_status; never infer completion from the dispatch response.
- **WF1 token lifecycle**: validate_task issues a single-use token with a short TTL; it
  authorizes the next mutating call; it is consumed on use.
- **Where to find state**: point to the resources below (protocol, sessions, audit) since
  the orchestrator can't read disk.
- **Active protocol/mode**: protocol_id, version, mode list, validator list, policy —
  interpolated from the loaded Protocol.

## Part 2 — Resources (read-only, URI-addressable)

All backed by existing managers (Protocol.model_dump(), SessionManager, _get_audit_log()) —
no new logic, just expose what's there. These are what a filesystem-less remote orchestrator
reads to build its model.

| URI | Content | Source |
|-----|---------|--------|
| snodo://protocol | modes, validators, constraints, disagreement policy | protocol.model_dump() |
| snodo://sessions | list of sessions (id, mode, current task, updated) | SessionManager.list_sessions |
| snodo://sessions/{session_id} | session detail: ordered task list, validator results, events | SessionManager + audit |
| snodo://audit | recent audit events (bounded, e.g. last 100) | _get_audit_log().get_history() |

The audit resource matters specifically for remote: it's how an orchestrator with no disk
sees what happened (it can't tail audit.log).

## Part 3 — Prompt (optional, low priority)

Optional `orchestrate` prompt template. Skip unless trivial — instructions + resources
already give the orchestrator what it needs.

## Constraints

- instructions built at server construction from the loaded protocol (dynamic id/version/
  modes/validators); static workflow + role model + async contract text.
- Resources are read-only and must work over SSE (no filesystem assumption in their handlers).
- No tool changes (all 20 stay). No transport changes (SSE already wired).
- Bound the audit resource (don't dump an unbounded log).

## Acceptance

- A fresh orchestrator (stdio or SSE) receives the workflow, role model, and async contract
  in `instructions` at connect — no nudging needed.
- A remote/SSE orchestrator with no filesystem can read protocol, session list, session
  detail, and recent audit entirely through resources.
- The async-contract section is explicit enough that an orchestrator polls get_job_status
  instead of inferring success from dispatch.
- No tool or transport changes.

## Tests

- instructions present in the initialize result; contains the workflow loop, the async
  contract, and the protocol's modes/validators.
- resources/list returns the 4 resources; resources/read returns expected content for each
  (protocol reflects loaded protocol; sessions lists sessions; session detail has tasks +
  events; audit returns bounded recent events).
- Resource handlers don't touch the filesystem in a way that assumes local access beyond
  the managers already used.
