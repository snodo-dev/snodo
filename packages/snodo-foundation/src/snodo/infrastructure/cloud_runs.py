"""Send locally emitted run records to the separate cloud run-record API."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from snodo.config import get_cloud_ingest_url, get_cloud_lease_url
from snodo.infrastructure.cloud_backoff import cloud_backoff_seconds, retry_after_seconds
from snodo.infrastructure.cloud_lease import get_admission_lease
from snodo.infrastructure.cloud_sync import CloudSyncState

_logger = logging.getLogger(__name__)

RUN_RECORD_SCHEMA = "snodo.run-record"
RUN_RECORD_SCHEMA_VERSION = 1
_MAX_RETRIES = 5


def send_run_record(record: dict[str, Any], config: dict[str, Any]) -> bool:
    """Attempt to send one local run record, never raising to its producer."""
    try:
        cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
        if not isinstance(cloud, dict):
            return False
        api_key = cloud.get("api_key", "")
        if not cloud.get("sync_enabled") or not isinstance(api_key, str) or not api_key.strip():
            return False

        api_url = get_cloud_ingest_url(config)
        lease = get_admission_lease(
            api_key,
            get_cloud_lease_url(config),
            session_id=str(record.get("task_id", "")),
            sync_state=CloudSyncState(),
        )
        if lease is None:
            return False

        payload = {
            "schema": RUN_RECORD_SCHEMA,
            "version": RUN_RECORD_SCHEMA_VERSION,
            "record": record,
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{api_url.rstrip('/')}/run-records/{quote(lease.lease_id, safe='')}"
        headers = {
            "Authorization": f"Bearer {lease.token}",
            "Content-Type": "application/json",
        }

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = httpx.post(url, content=body, headers=headers, timeout=30.0)
                if 200 <= response.status_code < 300:
                    return True
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < _MAX_RETRIES:
                        time.sleep(cloud_backoff_seconds(
                            attempt + 1, retry_after_seconds(response.headers),
                        ))
                        continue
                _logger.warning("Run record cloud send failed: HTTP %s", response.status_code)
                return False
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(cloud_backoff_seconds(attempt + 1))
                    continue
                _logger.warning("Run record cloud send failed: %s", exc)
                return False
    except Exception as exc:
        _logger.warning("Run record cloud send failed before delivery: %s", exc)
        return False


def run_record_payload_schema() -> dict[str, Any]:
    """Return the independently versioned JSON schema published by cloud schema."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "version", "record"],
        "properties": {
            "schema": {"const": RUN_RECORD_SCHEMA},
            "version": {"const": RUN_RECORD_SCHEMA_VERSION},
            "record": {"type": "object"},
        },
    }
