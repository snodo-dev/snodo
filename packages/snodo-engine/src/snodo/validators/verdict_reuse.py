"""Validate cached evidence before it can become a reused verdict.

This boundary deliberately takes today's question, not just its opaque key.
A malformed cache record is a miss, never an operational validator error.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from snodo.core.interfaces import ValidatorResult
from snodo.validators.verdict_cache import question_key


def reused_result(record: Any, key: str, question: dict) -> ValidatorResult | None:
    """Reconstruct only a genuine verdict answering exactly today's question."""
    if not isinstance(record, dict) or record.get("question") != question:
        return None
    if question_key(record["question"]) != key:
        return None
    if record.get("validator_id") != question["validator_id"]:
        return None
    if (record.get("error", False) or record.get("skipped", False)
            or not record.get("cacheable", True)):
        return None
    stored_at = record.get("stored_at")
    if (type(stored_at) not in (int, float) or not math.isfinite(stored_at)
            or stored_at <= 0):
        return None
    if not isinstance(record.get("justification"), str):
        return None
    try:
        return ValidatorResult(
            validator_id=record["validator_id"],
            severity=record["severity"],
            justification=record["justification"],
            cited_criteria=record.get("cited_criteria") or None,
            tool_access_missing=record.get("tool_access_missing"),
            reused=True,
            cache_provenance={
                "key": key,
                "question": copy.deepcopy(question),
                "stored_at": stored_at,
            },
        )
    except (ValueError, TypeError, KeyError):
        return None
