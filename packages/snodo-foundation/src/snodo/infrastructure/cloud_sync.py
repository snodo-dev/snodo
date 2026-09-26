"""Cloud sync infrastructure — cursor tracking + audit event dispatch.

FILE: snodo/infrastructure/cloud_sync.py

Manages per-session sync cursors (~/.snodo/cloud_sync.json) and
dispatches audit events to api.snodo.dev/i/{jti} in background threads.

Contract (from snodo-cloud ADR):
  POST api.snodo.dev/i/{jti}, lease Bearer auth, 1-50 events per batch,
  cursor advances on 200 only, 429 respects retry_after,
  5xx exponential backoff up to 5 retries, never raises.
"""

import atexit
import json
import logging
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, TypedDict, Union

from pydantic import (
    BaseModel, ConfigDict, Field, StrictStr, ValidationError, create_model,
    field_validator,
)

from snodo.infrastructure.atomic_json import atomic_write_json
from snodo.infrastructure.paths import resolve_home
from snodo.project import scope_for_project_id
from snodo.infrastructure.cloud_backoff import (
    cloud_backoff_seconds,
    retry_after_seconds,
)

_logger = logging.getLogger(__name__)


class CloudSyncEvent(TypedDict):
    """One audit event in the published ingest payload."""

    sequence: int
    timestamp: str
    event_type: str
    project_id: str
    scope: Literal["", "local", "remote"]
    data: Any
    previous_hash: str | None
    event_hash: str


class CloudSyncPayload(TypedDict):
    """The published audit ingest payload."""

    session_id: str
    project_path: str
    display_name: str
    events: list[CloudSyncEvent]

_MAX_BATCH_SIZE = 50
_MAX_RETRIES = 5
_DEFAULT_PAYLOAD_LIMIT = 5 * 1024 * 1024
_PAYLOAD_MARGIN = 256 * 1024

# These event tags and data fields were introduced with interface v6. A v5
# receiver must never see the new tags; hold at that point in the hash chain.
_V6_EVENT_TYPES = {"recon_started", "recon_completed", "plan_proposed", "plan_run"}
_V6_DATA_KEYS: dict[str, set[str]] = {
    "task_classified": {"plan_name", "plan_wave"},
    "dispatch": {"plan_name", "plan_wave"},
    "task_complete": {"plan_name", "plan_wave"},
    "task_merged": {
        "plan_name", "plan_wave", "base_sha", "commit_count", "commits",
        "files_changed", "insertions", "deletions",
    },
    "halt": {"plan_name", "plan_wave"},
}


def _requires_v6(event: Any) -> bool:
    """Whether transmitting this event verbatim requires interface v6."""
    return event.event_type in _V6_EVENT_TYPES or bool(
        _V6_DATA_KEYS.get(event.event_type, set()) & set((event.data or {}).keys())
        if isinstance(event.data, dict) else False
    )


_EVENT_DATA_KEYS: dict[str, tuple[str, ...]] = {
    "project_announced": ("project_id", "scope", "display_name"),
    "readiness_checked": (
        "project_id", "scope", "display_name", "protocol_id", "score",
        "total_checks", "passed_checks", "repository_findings_count",
        "workstation_findings_count", "findings",
    ),
    "dispatch": ("task_ref", "mode", "token_id", "artifacts_count"),
    "work_already_present": ("task_ref", "base_ref", "artifacts_count", "files"),
    "governance_check": ("task_ref", "mode", "constraints_checked"),
    "validate": ("phase", "task_ref", "validators_invoked", "results", "outcome", "policy_decision"),
    "task_classified": ("task_ref", "flow_type", "wave_id", "task_summary"),
    "wave_created": ("wave_id", "feature_description"),
    "task_complete": ("task_ref", "artifacts", "session_id", "commit", "change_size"),
    "task_merged": ("task_ref", "branch", "merge_sha", "spec", "session_id"),
    "halt": ("task_ref", "reason", "blocker_validators", "halt_type", "raw_halt_type"),
    "transition": ("from_mode", "to_mode", "task_ref"),
    "token_consumed": ("task_ref", "session_id"),
    "post_validation_route": ("decision", "task_ref"),
    "post_validate_bypassed": ("mode", "reason", "task_ref"),
    "session_started": ("session_id", "mode", "project_root"),
    "session_task_changed": ("old_task", "new_task"),
    "session_decision_updated": ("key", "value"),
    "recovery_resolved": ("depth", "attempts_used"),
    "recovery_internal_error": ("depth", "error"),
    "execution_failed": ("error", "task_ref"),
    "verification_executed": (
        "command", "commit", "returncode", "outcome", "validator_id",
        "working_directory", "output_tail",
    ),
    "coder_test_run": ("command_type", "exit_code", "test_path", "turn_index", "job_id"),
    "test_modified": ("mutations", "task_id", "job_id"),
    "unverified_merge_blocked": ("task_ref", "branch", "target_commit", "reason", "session_id"),
    # The event tags below are part of the hash-chained audit stream. Their
    # data remains an opaque object until an ingest consumer needs a pinned
    # shape; accepting it here must not break delivery of later chain entries.
    "coder_respawned": (),
    "coder_timed_out": (),
    "coder_turn_budget_exhausted": (),
    "coder_unavailable": (),
    "adjudication_carry_forward": (),
    "decision_record_issued": (),
    "set_model_verify_failed": (),
    "token_blocked": (),
    "token_expired": (),
    "token_invalid": (),
    "token_issued": (),
    "token_task_mismatch": (),
    "decision_record_task_mismatch": (),
    "decision_record_invalid": (),
    "disagreement_escalated": (),
    "disagreement_resolved": (),
    "dispatch_refused_coder_unavailable": (),
    "dispatch_request": (),
    "environment_prep_failed": (),
    "head_not_moved": (),
    "human_review_recorded": (),
    "job_state_corrupt": (),
    "merge_conflict_escalated": (),
    "merge_failed_escalated": (),
    "mode_change": (),
    "no_file_operations": (),
    "plan_proposed": (),
    "plan_run": (),
    "protected_path_blocked": (),
    "protected_paths_unchecked": (),
    "recovery_exhausted": (),
    "recovery_stalled": (),
    "session_audited_but_missing": (),
    "session_corrupt": (),
    "session_deleted": (),
    "session_memory_updated": (),
    "session_pointer_audited_but_missing": (),
    "session_resumed": (),
    "severity_cap_applied": (),
    "snodo_mutation_blocked": (),
    "spec_authored": (),
    "spec_authored_failed": (),
    "spec_premise_stale": (),
    "spec_replaced": (),
    "subtask_spawned": (),
    "task_add_rejected": (),
    "task_added": (),
    "task_replaced": (),
    "task_status_corrected": (),
    "task_unmerged": (),
    "token_store_unavailable": (),
    "tool_call": (),
    "validator_contradiction_detected": (),
    "validator_results": (),
    "wf3_runtime_violation": (),
    "worktree_isolation_failed": (),
}


_EVENT_DATA_KEYS_V5 = _EVENT_DATA_KEYS
_EVENT_DATA_KEYS_V6: dict[str, tuple[str, ...]] = {
    **_EVENT_DATA_KEYS_V5,
    "task_classified": (*_EVENT_DATA_KEYS_V5["task_classified"], "plan_name", "plan_wave"),
    "dispatch": (*_EVENT_DATA_KEYS_V5["dispatch"], "plan_name", "plan_wave"),
    "task_complete": (*_EVENT_DATA_KEYS_V5["task_complete"], "plan_name", "plan_wave"),
    "task_merged": (
        *_EVENT_DATA_KEYS_V5["task_merged"], "base_sha", "commit_count", "commits",
        "files_changed", "insertions", "deletions", "plan_name", "plan_wave",
    ),
    "halt": (*_EVENT_DATA_KEYS_V5["halt"], "plan_name", "plan_wave"),
    "plan_proposed": ("plan_name", "waves"),
    "plan_run": ("plan_name", "waves", "trigger", "queue", "job_id", "mode"),
    "recon_started": (
        "recon_id", "query", "paths", "agent_count", "agent_models", "session_id", "created_at",
    ),
    "recon_completed": (
        "recon_id", "status", "succeeded_agents", "failed_agents", "duration", "completed_at", "summary",
    ),
}


class PlanWaveShape(BaseModel):
    """Optional pinned wave shape used by plan history events."""

    model_config = ConfigDict(extra="allow")

    wave_id: str | int | None = None
    task_refs: list[str] | None = None


class PlanProposedData(BaseModel):
    """Optional pinned shape for a proposed plan."""

    model_config = ConfigDict(extra="allow")

    plan_name: str | None = None
    waves: list[PlanWaveShape] | None = None


class PlanRunData(PlanProposedData):
    """Optional pinned plan shape plus run trigger, queue, job, and mode."""

    trigger: Literal["mcp", "cli", "queue"] | None = None
    queue: str | None = None
    job_id: str | None = None
    mode: str | None = None


def _event_models(
    event_data_keys: dict[str, tuple[str, ...]],
    data_models: dict[str, type[BaseModel]] | None = None,
) -> tuple[type[BaseModel], ...]:
    """Build one schema branch for each event type in the cloud contract."""
    models = []
    data_models = data_models or {}
    for event_type, keys in event_data_keys.items():
        data_model = create_model(
            f"{event_type.title().replace('_', '')}Data",
            __config__=ConfigDict(extra="allow"),
            **{
                key: (data_models[event_type].model_fields[key].annotation, None)
                if event_type in data_models and key in data_models[event_type].model_fields
                else (Any | None, None)
                for key in keys
            },
        )
        models.append(create_model(
            f"{event_type.title().replace('_', '')}Event",
            __config__=ConfigDict(extra="forbid"),
            sequence=(int, ...),
            timestamp=(Annotated[StrictStr, Field(json_schema_extra={"format": "date-time"})], ...),
            event_type=(Literal[event_type], ...),
            project_id=(str, ...),
            scope=(Literal["", "local", "remote"], ...),
            data=(data_model, ...),
            previous_hash=(str, ...),
            event_hash=(str, ...),
        ))
    return tuple(models)


class OpaqueAuditEvent(BaseModel):
    """Envelope for historical or otherwise undeclared audit event types."""

    model_config = ConfigDict(extra="forbid")

    sequence: int
    timestamp: Annotated[StrictStr, Field(json_schema_extra={"format": "date-time"})]
    event_type: Annotated[
        StrictStr,
        Field(json_schema_extra={"not": {"enum": list(_EVENT_DATA_KEYS)}}),
    ]
    project_id: str
    scope: Literal["", "local", "remote"]
    data: Any
    previous_hash: str
    event_hash: str

    @field_validator("event_type")
    @classmethod
    def event_type_must_be_undeclared(cls, value: str) -> str:
        if value in _EVENT_DATA_KEYS:
            raise ValueError("declared event types must match their declared schema")
        return value


class OpaqueAuditEventV6(BaseModel):
    """V6 envelope for historical tags outside the declared ingest union."""

    model_config = ConfigDict(extra="forbid")

    sequence: int
    timestamp: Annotated[StrictStr, Field(json_schema_extra={"format": "date-time"})]
    event_type: Annotated[
        StrictStr,
        Field(json_schema_extra={"not": {"enum": list(_EVENT_DATA_KEYS_V6)}}),
    ]
    project_id: str
    scope: Literal["", "local", "remote"]
    data: Any
    previous_hash: str
    event_hash: str

    @field_validator("event_type")
    @classmethod
    def event_type_must_be_undeclared(cls, value: str) -> str:
        if value in _EVENT_DATA_KEYS_V6:
            raise ValueError("declared event types must match their declared schema")
        return value


_V5_EVENT_MODELS = _event_models(_EVENT_DATA_KEYS_V5)
_V6_EVENT_MODELS = _event_models(
    _EVENT_DATA_KEYS_V6,
    {"plan_proposed": PlanProposedData, "plan_run": PlanRunData},
)

# Known tags retain their pinned data shapes. Historical tags not declared by
# this client use the same validated envelope with opaque event data.
AuditEventEnvelope = Union[*_V5_EVENT_MODELS, OpaqueAuditEvent]
AuditEventEnvelopeV6 = Union[*_V6_EVENT_MODELS, OpaqueAuditEventV6]


class AuditIngestBatch(BaseModel):
    """A cloud ingest request, including its existing 1--50 event bound."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_path: str
    display_name: str
    events: list[AuditEventEnvelope] = Field(min_length=1, max_length=_MAX_BATCH_SIZE)


class AuditIngestBatchV6(BaseModel):
    """The v6 ingest shape; v5 remains the active sender contract for fallback."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_path: str
    display_name: str
    events: list[AuditEventEnvelopeV6] = Field(min_length=1, max_length=_MAX_BATCH_SIZE)


def _payload_for_events(session_id: str, project_root: str, events: list) -> dict:
    """Build the exact wire payload used for size measurement and delivery."""
    payload_events = []
    for ev in events:
        pid = getattr(ev, "project_id", "")
        project_id_str = pid if isinstance(pid, str) else ""
        payload_events.append({
            "sequence": ev.sequence,
            "timestamp": ev.timestamp,
            "event_type": ev.event_type,
            "project_id": project_id_str,
            "scope": scope_for_project_id(project_id_str),
            "data": ev.data,
            "previous_hash": ev.previous_hash,
            "event_hash": ev.event_hash,
        })
    return {
        "session_id": session_id,
        "project_path": project_root,
        "display_name": Path(project_root).name if project_root else "",
        "events": payload_events,
    }


def _v5_payload(payload: dict, interface_version: int) -> Optional[dict]:
    """Select the v5-compatible prefix, or hold if it starts with v6 data.

    Hashes describe the local audit data and are intentionally unchanged by
    projection. Sequence, previous_hash and event_hash always travel together;
    once an event needs v6, it and the remainder of the chain stay held.
    """
    if interface_version >= 6:
        return payload
    projected = {**payload, "events": []}
    for event in payload["events"]:
        if event["event_type"] in _V6_EVENT_TYPES:
            break
        data = event["data"]
        removed = _V6_DATA_KEYS.get(event["event_type"], set())
        if removed and isinstance(data, dict) and removed.intersection(data):
            # Stripping these fields would make event_hash no longer attest to
            # the transmitted data. Hold the event and chain suffix intact.
            break
        projected["events"].append(event)
    return projected if projected["events"] else None


def _encode_payload(payload: dict) -> bytes:
    """Serialize exactly as the ingest POST does."""
    return json.dumps(payload).encode()


def _response_limit_bytes(response: Any) -> Optional[int]:
    """Read an optional server payload limit from JSON or diagnostic text."""
    try:
        value = response.json().get("limit_bytes")
        if isinstance(value, int) and value > 0:
            return value
    except (AttributeError, TypeError, ValueError):
        pass
    match = re.search(r'["\']?limit_bytes["\']?\s*[:=]\s*(\d+)', response.text)
    return int(match.group(1)) if match else None


def _response_retry_after_seconds(response: Any) -> Optional[float]:
    """Read the server's retry delay from Retry-After or its JSON body."""
    headers = getattr(response, "headers", None)
    header_delay = retry_after_seconds(headers)
    if header_delay is not None:
        return header_delay
    try:
        value = response.json().get("retry_after")
    except (AttributeError, TypeError, ValueError):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    if isinstance(value, str):
        return retry_after_seconds({"Retry-After": value})
    return None


def _partition_events(
    session_id: str, project_root: str, events: list, byte_limit: int,
) -> list[list]:
    """Greedily group events under both wire limits."""
    batches: list[list] = []
    batch: list = []
    for event in events:
        candidate = batch + [event]
        too_many = len(candidate) > _MAX_BATCH_SIZE
        too_large = len(_encode_payload(
            _payload_for_events(session_id, project_root, candidate),
        )) > byte_limit
        if batch and (too_many or too_large):
            batches.append(batch)
            batch = [event]
        else:
            batch = candidate
    if batch:
        batches.append(batch)
    return batches


class CloudSyncState:
    """Tracks per-session sync progress in ~/.snodo/cloud_sync.json.

    Atomic writes (tmp + rename), matching the agents.json pattern.
    """

    def __init__(self, state_path: Optional[Path] = None):
        self._path = state_path or resolve_home() / "cloud_sync.json"

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        atomic_write_json(self._path, data, trailing_newline=True)

    def get_cursor(self, session_id: str) -> int:
        """Return last_synced_sequence for *session_id* (0 if never synced)."""
        data = self._load()
        session = data.get(session_id)
        if isinstance(session, dict):
            return session.get("last_synced_sequence", 0)
        return 0

    def advance_cursor(self, session_id: str, sequence: int) -> None:
        """Record that events up to *sequence* have been synced.

        A confirmed success clears a refusal, the pending count, and any
        recorded error, so ``cloud status`` reflects a healthy session
        (Fixes #141, #142).
        """
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        sess = data[session_id]
        sess["last_synced_sequence"] = sequence
        sess["last_synced_at"] = time.time()
        sess["refused"] = False
        sess.pop("refused_reason", None)
        sess.pop("refused_range", None)
        sess.pop("refused_at", None)
        sess.pop("refused_status_code", None)
        sess["pending_count"] = 0
        sess.pop("last_error", None)
        self._save(data)

    def record_attempt(
        self, session_id: str, pending: int, error: Optional[str] = None,
    ) -> None:
        """Record a sync attempt outcome for *session_id* (Fixes #142).

        Stores how many events were pending, when the attempt happened, and
        what went wrong (if it failed) so ``cloud status`` can answer "is my
        audit trail actually reaching the cloud?".
        """
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        data[session_id]["pending_count"] = pending
        data[session_id]["last_attempt_at"] = time.time()
        if error is not None:
            data[session_id]["last_error"] = error
        self._save(data)

    def record_liveness_push(self, session_id: str, error: Optional[str] = None) -> None:
        """Record the most recent liveness attempt, independently of audit sync."""
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        sess = data[session_id]
        sess["last_liveness_push_at"] = time.time()
        if error is None:
            sess.pop("last_liveness_error", None)
        else:
            sess["last_liveness_error"] = error
            sess["liveness_failure_count"] = sess.get("liveness_failure_count", 0) + 1
        self._save(data)

    def record_refusal(
        self,
        session_id: str,
        reason: str,
        first_seq: Optional[int] = None,
        last_seq: Optional[int] = None,
        status_code: Optional[int] = None,
    ) -> None:
        """Record that a batch or admission for *session_id* was refused by the cloud server."""
        data = self._load()
        if session_id not in data or not isinstance(data.get(session_id), dict):
            data[session_id] = {}
        sess = data[session_id]
        sess["refused"] = True
        sess["refused_reason"] = reason
        if first_seq is not None and last_seq is not None:
            sess["refused_range"] = [first_seq, last_seq]
        else:
            sess.pop("refused_range", None)
        sess["refused_at"] = time.time()
        if status_code is not None:
            sess["refused_status_code"] = status_code
        self._save(data)

    def clear_refusal(self, session_id: str) -> None:
        """Clear refused status for *session_id*."""
        data = self._load()
        if session_id in data and isinstance(data[session_id], dict):
            sess = data[session_id]
            sess["refused"] = False
            sess.pop("refused_reason", None)
            sess.pop("refused_range", None)
            sess.pop("refused_at", None)
            sess.pop("refused_status_code", None)
        self._save(data)

    def is_refused(self, session_id: str) -> bool:
        """Return True only for a refusal under the current terminal rules.

        Older state files may contain refusals for route misses (404) or
        oversized batches (413). Neither is a terminal admission refusal.
        The legacy empty-key entry is intentionally ignored: refusals belong
        to one session and must never silence every session.
        """
        data = self._load()
        sess = data.get(session_id)
        if not isinstance(sess, dict) or not sess.get("refused"):
            return False
        status = sess.get("refused_status_code")
        if status == 413:
            return "single event sequence" in str(sess.get("refused_reason", ""))
        return status is None or (
            isinstance(status, int) and 400 <= status < 500
            and status not in (404, 413, 429)
        )

    def get_summary(self) -> dict:
        """Return full per-session sync summary."""
        return self._load()


class CloudSyncDispatcher:
    """Dispatches unsynced audit events to snodo cloud.

    Runs in a background thread — never blocks the caller, never raises.
    """

    def sync(
        self,
        session_id: str,
        project_root: str,
        audit_log: Any,
        api_key: str,
        api_url: str,
        force: bool = False,
        lease_url: Optional[str] = None,
    ) -> dict:
        """Sync audit events since the last cursor.

        Args:
            session_id: Current session identifier
            project_root: Absolute project path
            audit_log: AuditLog instance (provides .events)
            api_key: Snodo cloud API key
            api_url: Base URL for the ingest API
            force: If True, re-attempt sync even if session is in refused state

        Returns:
            ``{"synced": int, "failed": bool, "refused": bool, "reason": str,
                "pending": int}``
        """
        try:
            return self._sync_impl(
                session_id, project_root, audit_log, api_key, api_url,
                force=force, lease_url=lease_url,
            )
        except Exception:
            _logger.warning("Cloud sync threw unexpected exception", exc_info=True)
            return {"synced": 0, "failed": True, "pending": 0}

    def _sync_impl(
        self,
        session_id: str,
        project_root: str,
        audit_log: Any,
        api_key: str,
        api_url: str,
        force: bool = False,
        lease_url: Optional[str] = None,
    ) -> dict:
        events = getattr(audit_log, "events", [])
        if not events:
            return {"synced": 0, "failed": False, "pending": 0}

        state = CloudSyncState()

        cursor = state.get_cursor(session_id)

        # Collect unsynced events
        unsynced: list = []
        for ev in events:
            if ev.sequence > cursor:
                unsynced.append(ev)

        if not unsynced:
            if state.is_refused(session_id):
                info = state.get_summary().get(session_id, {})
                reason = info.get("refused_reason", "refused by server")
                pending = info.get("pending_count", 0)
                state.record_attempt(session_id, pending=pending, error=reason)
                return {
                    "synced": 0, "failed": True, "refused": True,
                    "reason": reason, "pending": pending,
                }
            return {"synced": 0, "failed": False, "pending": 0}

        if not force and state.is_refused(session_id):
            info = state._load().get(session_id, {})
            reason = info.get("refused_reason", "refused by server")
            pending = len(unsynced)
            state.record_attempt(session_id, pending=pending, error=reason)
            _logger.info("Skipping automatic cloud sync for refused session %s: %s", session_id, reason)
            return {"synced": 0, "failed": True, "refused": True, "reason": reason, "pending": pending}

        synced = 0
        failed = False
        refused = False
        refused_reason = None
        last_error: Optional[str] = None

        # Partition by both event count and the actual serialized wire size.
        # The margin keeps normal batches below the server's object limit.
        payload_limit = _DEFAULT_PAYLOAD_LIMIT - _PAYLOAD_MARGIN
        queue = _partition_events(session_id, project_root, unsynced, payload_limit)
        # Keep v6-only tags at batch boundaries. A v5 cloud can then accept the
        # compatible prefix before the first event it must hold.
        separated: list[list] = []
        for candidate in queue:
            segment: list = []
            for event in candidate:
                if _requires_v6(event):
                    if segment:
                        separated.append(segment)
                        segment = []
                    separated.append([event])
                else:
                    segment.append(event)
            if segment:
                separated.append(segment)
        queue = separated
        oversized_sequences: list[int] = []
        while queue:
            batch = queue.pop(0)
            first_seq = batch[0].sequence
            max_seq = batch[-1].sequence
            outcome, reason, status_code = self._post_batch(
                session_id, project_root, batch, api_key, api_url,
                force=force or bool(oversized_sequences),
                lease_url=lease_url,
            )

            if outcome == "delivered":
                state.clear_refusal(session_id)
                state.advance_cursor(session_id, max_seq)
                _logger.debug("Cursor advanced to sequence %d", max_seq)
                synced += len(batch)
            elif outcome == "too_large" and len(batch) > 1:
                server_limit = status_code or _DEFAULT_PAYLOAD_LIMIT
                payload_limit = min(payload_limit, max(1, server_limit - _PAYLOAD_MARGIN))
                smaller = _partition_events(
                    session_id, project_root, batch, payload_limit,
                )
                # The server may enforce an undisclosed smaller limit. A 413
                # must always reduce the next request, even if its advertised
                # limit would fit the request that just failed.
                if len(smaller) == 1 and len(smaller[0]) == len(batch):
                    midpoint = len(batch) // 2
                    smaller = [batch[:midpoint], batch[midpoint:]]
                queue[0:0] = smaller
            elif outcome == "too_large":
                event_size = len(_encode_payload(_payload_for_events(
                    session_id, project_root, batch)))
                detailed_reason = f"{reason}; single event sequence {first_seq} is {event_size} bytes"
                state.record_refusal(
                    session_id, reason=detailed_reason, first_seq=first_seq,
                    last_seq=max_seq, status_code=413,
                )
                refused = True
                failed = True
                refused_reason = detailed_reason
                last_error = detailed_reason
                oversized_sequences.append(first_seq)
                # A single unstoreable event must not prevent later events
                # from being attempted. Successful later batches advance the
                # high-water cursor, while refused_range preserves the gap.
                continue
            elif outcome == "refused":
                state.record_refusal(
                    session_id,
                    reason=reason,
                    first_seq=first_seq,
                    last_seq=max_seq,
                    status_code=status_code,
                )
                failed = True
                refused = True
                refused_reason = reason
                last_error = reason
                break
            elif outcome == "unsupported":
                # Capability is unknown or still v5. Keep this sequence and
                # everything after it pending; advancing past it would create
                # a gap in the cloud's hash chain.
                failed = True
                last_error = reason
                break
            else:  # retryable
                failed = True
                last_error = reason
                break

            if outcome == "delivered" and oversized_sequences:
                # Keep the single-event refusal visible while allowing the
                # cursor to progress past later events.
                state.record_refusal(
                    session_id,
                    reason=refused_reason or "single event exceeds cloud payload limit",
                    first_seq=oversized_sequences[0],
                    last_seq=oversized_sequences[0],
                    status_code=413,
                )

        pending = max(0, len(unsynced) - synced)
        state.record_attempt(session_id, pending=pending, error=last_error if failed else None)

        res_dict: dict = {"synced": synced, "failed": failed, "pending": pending}
        if failed and last_error:
            res_dict["reason"] = last_error
        if refused:
            res_dict["refused"] = True
            res_dict["reason"] = refused_reason
        return res_dict

    def _post_batch(
        self,
        session_id: str,
        project_root: str,
        batch: list,
        api_key: str,
        api_url: str,
        force: bool = False,
        lease_url: Optional[str] = None,
    ) -> tuple:
        """POST a batch of events.

        Returns:
            (outcome, reason, status_code) where outcome is one of:
            - "delivered": HTTP 2xx
            - "retryable": HTTP 404 (route mismatch), 429, 5xx, or network error
            - "refused": HTTP 4xx other than 404/429, including invalid batches
        """
        import httpx

        payload = _payload_for_events(session_id, project_root, batch)
        from urllib.parse import quote

        from snodo.config import get_cloud_lease_url
        from snodo.infrastructure.cloud_lease import (
            get_admission_lease, invalidate_lease,
        )

        state = CloudSyncState()
        lease_url = lease_url or get_cloud_lease_url({"cloud": {"api_url": api_url}})
        lease = get_admission_lease(api_key, lease_url, session_id=session_id, sync_state=state, force=force)
        if lease is None:
            if state.is_refused(session_id):
                info = state._load().get(session_id, {})
                reason = info.get("refused_reason", "Cloud admission refused")
                return ("refused", reason, info.get("refused_status_code", 401))
            from snodo.infrastructure.cloud_lease import get_last_admission_error
            return ("retryable", get_last_admission_error() or "Cloud admission unreachable: no response received", None)

        advertised_version = getattr(lease, "interface_version", None)
        interface_version = advertised_version or 5
        payload = _v5_payload(payload, interface_version)
        if payload is None:
            reason = "Cloud interface v6 is not yet advertised; event batch held for retry"
            # A later attempt must ask the cloud again rather than trusting an
            # unknown or stale v5 capability cached in this admission lease.
            invalidate_lease(lease)
            return ("unsupported", reason, interface_version)
        # Validate the exact projected payload without re-serializing it: its
        # hash-chain envelope remains byte-for-byte unchanged.
        try:
            AuditIngestBatch.model_validate(payload)
        except ValidationError as err:
            event_index = next(
                (part for error in err.errors() for part in error.get("loc", ())
                 if isinstance(part, int)), None,
            )
            payload_events = payload["events"]
            rejected = payload_events[event_index] if event_index is not None and event_index < len(payload_events) else None
            event_label = (
                f"event {rejected['event_type']!r} at sequence {rejected['sequence']}"
                if rejected else "ingest batch"
            )
            first_error = err.errors()[0] if err.errors() else {}
            detail = first_error.get("msg", "invalid payload")
            reason = (
                f"Client-side cloud sync validation failed for {event_label}: {detail}. "
                "Check the event envelope, then retry with `snodo cloud sync --all --force`."
            )
            _logger.error(reason)
            return ("retryable", reason, None)
        body = _encode_payload(payload)

        url = f"{api_url.rstrip('/')}/i/{quote(lease.jti, safe='')}"
        first_seq = batch[0].sequence
        last_seq = batch[-1].sequence
        _logger.debug(
            "POST %s — %d events (seq %d-%d)",
            url, len(batch), first_seq, last_seq,
        )

        headers = {
            "Authorization": f"Bearer {lease.token}",
            "Content-Type": "application/json",
        }
        lease_replaced = False

        attempt = 0
        while attempt <= _MAX_RETRIES:
            try:
                response = httpx.post(
                    url, content=body, headers=headers, timeout=30.0,
                )

                if 200 <= response.status_code < 300:
                    _logger.debug("Response %d — accepted=%s",
                                  response.status_code, response.text[:200])
                    return ("delivered", f"HTTP {response.status_code}", response.status_code)

                body_text = response.text[:500]
                reason = f"{url} -> HTTP {response.status_code}: {body_text.strip() or 'No server message'}"

                if response.status_code == 401 and not lease_replaced:
                    print(f"Cloud ingest rejected lease: {reason}; minting once and retrying", file=__import__("sys").stderr)
                    invalidate_lease(lease)
                    lease = get_admission_lease(api_key, lease_url, session_id=session_id, sync_state=state, force=True)
                    if lease is None:
                        from snodo.infrastructure.cloud_lease import get_last_admission_error
                        return ("retryable", get_last_admission_error() or reason, None)
                    url = f"{api_url.rstrip('/')}/i/{quote(lease.jti, safe='')}"
                    headers["Authorization"] = f"Bearer {lease.token}"
                    lease_replaced = True
                    attempt += 1
                    continue

                if response.status_code == 429:
                    retry_after = _response_retry_after_seconds(response)
                    wait = cloud_backoff_seconds(
                        attempt + 1, retry_after,
                    )
                    _logger.warning(
                        "Cloud sync HTTP 429 retry_after=%s (session=%s): %s",
                        retry_after, session_id, body_text,
                    )
                    print(f"Cloud ingest failed: {reason}", file=__import__("sys").stderr)
                    time.sleep(wait)
                    continue

                if response.status_code >= 500:
                    if attempt == _MAX_RETRIES:
                        _logger.warning(
                            "Cloud sync HTTP %d retries exhausted (session=%s): %s",
                            response.status_code, session_id, body_text,
                        )
                        print(f"Cloud ingest failed: {reason}", file=__import__("sys").stderr)
                        return ("retryable", f"HTTP {response.status_code}: {body_text}", response.status_code)
                    _logger.warning(
                        "Cloud sync HTTP %d attempt %d (session=%s): %s",
                        response.status_code, attempt, session_id, body_text,
                    )
                    time.sleep(cloud_backoff_seconds(attempt + 1, retry_after_seconds(response.headers)))
                    attempt += 1
                    continue

                if response.status_code == 413:
                    limit_bytes = _response_limit_bytes(response)
                    return ("too_large", reason, limit_bytes or _DEFAULT_PAYLOAD_LIMIT)

                print(f"Cloud ingest failed: {reason}", file=__import__("sys").stderr)
                if response.status_code == 404:
                    return ("retryable", reason, response.status_code)
                _logger.warning(
                    "Cloud sync HTTP %d REFUSED on session=%s: %s",
                    response.status_code, session_id, body_text,
                )
                return ("refused", reason, response.status_code)

            except Exception as exc:
                if attempt == _MAX_RETRIES:
                    _logger.warning(
                        "Cloud sync network error retries exhausted (session=%s): %s",
                        session_id, exc, exc_info=True,
                    )
                    reason = f"{url} -> no response: {exc}"
                    print(f"Cloud ingest failed: {reason}", file=__import__("sys").stderr)
                    return ("retryable", reason, None)
                time.sleep(cloud_backoff_seconds(attempt + 1))
                attempt += 1

        return ("retryable", "Retries exhausted", None)


def _should_sync(config: Optional[dict] = None) -> bool:
    """Return True if cloud sync is enabled and an API key is configured."""
    if config is None:
        from snodo.config import ConfigManager
        config = ConfigManager().load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    return bool(cloud.get("sync_enabled")) and bool(cloud.get("api_key", "").strip())


#: How long the flush waits for background syncs to finish before giving up.
#: Bounded, always — a slow or unreachable cloud must never hang the process.
#: The whole flush fits inside one budget, however many syncs are pending; a
#: sync that needs longer is abandoned, the cursor is left where it was, and
#: the operator is told on stderr (Fixes #142).
_SYNC_WAIT_BUDGET = 5.0

#: Pending background syncs registered by ``sync_if_enabled``, drained once at
#: process exit by ``flush_pending_syncs``. Each entry carries the thread, its
#: result dict, the session id, and the audit log for the abandoned-case count.
_pending_syncs: list = []


def _pending_count(audit_log: Any, session_id: str) -> int:
    """Return the number of unsynced events for *session_id*.

    The unsynced backlog — events past the cursor — not the size of the whole
    log. This is the same measurement the sync itself uses, so the timeout and
    failure branches of the flush report the same thing (Fixes #142).
    """
    events = getattr(audit_log, "events", [])
    if not events:
        return 0
    cursor = CloudSyncState().get_cursor(session_id)
    return sum(1 for ev in events if ev.sequence > cursor)


def flush_pending_syncs() -> None:
    """Join pending background syncs with a single bounded wait and report.

    Registered via ``atexit`` so a sync that would succeed in a few seconds
    gets those seconds at process exit, exactly once — not once per task in a
    multi-task plan. The whole flush fits inside one ``_SYNC_WAIT_BUDGET``
    regardless of how many syncs are pending: threads are joined in turn, each
    for at most the remaining budget, and anything still running when the
    budget is spent is abandoned and reported. The wait is always bounded:
    threads stay daemon, so a slow or unreachable cloud never hangs the
    process. A sync that fails, or that is abandoned because it ran out of
    time, is reported on stderr in one line and the cursor is left where it
    was (events re-send next time) (Fixes #142).
    """
    import sys

    deadline = time.monotonic() + _SYNC_WAIT_BUDGET

    while _pending_syncs:
        thread, result, session_id, audit_log = _pending_syncs.pop(0)
        if thread.is_alive():
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(timeout=remaining)
        if thread.is_alive():
            pending = _pending_count(audit_log, session_id)
            print(
                f"⚠ cloud sync still in progress for {session_id} — "
                f"{pending} event(s) pending; run `snodo cloud sync --all` to finish.",
                file=sys.stderr,
            )
            continue
        if result.get("failed"):
            pending = result.get("pending", _pending_count(audit_log, session_id))
            if result.get("refused"):
                print(
                    f"⚠ cloud sync refused for {session_id} — {pending} event(s) unsent: "
                    f"{result.get('reason', 'server refused the request')}; "
                    "run `snodo cloud sync --force` to retry.",
                    file=sys.stderr,
                )
                continue
            print(
                f"⚠ cloud sync failed for {session_id} — "
                f"{pending} event(s) pending; run `snodo cloud sync --all` to retry.",
                file=sys.stderr,
            )


atexit.register(flush_pending_syncs)


def sync_if_enabled(
    session_id: str,
    project_root: str,
    audit_log: Any,
    config: Optional[dict] = None,
) -> None:
    """Start a best-effort cloud sync if enabled and register it for the
    bounded wait at process exit (Fixes #142).

    The sync runs in a daemon thread and is joined with a bounded timeout by
    ``flush_pending_syncs`` at exit — once per process, whichever task started
    it. The CLI itself never blocks, and a slow cloud never hangs the process.
    """
    from threading import Thread

    if not _should_sync(config):
        return

    if config is None:
        from snodo.config import ConfigManager
        config = ConfigManager().load()

    from snodo.config import get_cloud_ingest_url, get_cloud_lease_url

    cloud = config.get("cloud", {})
    api_key = cloud["api_key"]
    # cloud.api_url is the ingest base. Tunnel provisioning uses its own
    # key (cloud.tunnel_api_url) — never route one service's requests to
    # the other's host.
    api_url = get_cloud_ingest_url(config)
    lease_url = get_cloud_lease_url(config)

    dispatcher = CloudSyncDispatcher()
    result: dict = {"synced": 0, "failed": False, "pending": 0}

    def _run_sync():
        try:
            result.update(dispatcher.sync(
                session_id, project_root, audit_log, api_key, api_url,
                lease_url=lease_url,
            ))
        except Exception as e:
            _logger.warning("Cloud sync background thread failed: %s", e)
            result["failed"] = True

    thread = Thread(target=_run_sync, daemon=True)
    thread.start()
    _pending_syncs.append((thread, result, session_id, audit_log))
