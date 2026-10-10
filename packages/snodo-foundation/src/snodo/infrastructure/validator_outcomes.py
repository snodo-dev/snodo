"""Read-only per-validator statistics derived from validation audit events."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

SEVERITIES = ("pass", "warn", "blocker")
PHASES = ("pre", "post")


@dataclass
class ValidatorPhaseStats:
    """Verdict counts and blocker rate for one validator in one phase."""

    severity_counts: Counter[str] = field(default_factory=Counter)
    reused_severity_counts: Counter[str] = field(default_factory=Counter)

    @property
    def verdict_count(self) -> int:
        return sum(self.severity_counts.values())

    @property
    def reused_count(self) -> int:
        return sum(self.reused_severity_counts.values())

    @property
    def block_rate(self) -> float | None:
        return (
            self.severity_counts["blocker"] / self.verdict_count
            if self.verdict_count
            else None
        )


@dataclass
class ValidatorOutcomeStats:
    """Per-validator/per-phase statistics and skipped malformed record count."""

    validators: dict[str, dict[str, ValidatorPhaseStats]] = field(default_factory=dict)
    skipped_events: int = 0


def aggregate_validator_outcomes(
    events: Iterable[Any],
    *,
    days: float | None = None,
    now: datetime | None = None,
) -> ValidatorOutcomeStats:
    """Aggregate validate events, excluding reused verdicts from block rates.

    ``events`` may contain audit-event objects or decoded event dictionaries.
    When ``days`` is supplied, events older than ``now - days`` are ignored;
    events with unusable timestamps are counted as skipped. A validator listed
    in ``validators_invoked`` is represented even if it has no verdict.
    """
    if days is not None and days < 0:
        raise ValueError("days must be non-negative")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    cutoff = current - timedelta(days=days) if days is not None else None
    stats = ValidatorOutcomeStats()

    for event in events:
        event_type, data, timestamp = _event_parts(event)
        if event_type != "validate":
            continue
        if not isinstance(data, dict):
            stats.skipped_events += 1
            continue
        if cutoff is not None:
            parsed = _timestamp(timestamp)
            if parsed is None:
                stats.skipped_events += 1
                continue
            if parsed < cutoff:
                continue
        phase = data.get("phase")
        invoked = data.get("validators_invoked", [])
        results = data.get("results", [])
        if (
            phase not in PHASES
            or not isinstance(invoked, list)
            or not isinstance(results, list)
        ):
            stats.skipped_events += 1
            continue
        for validator_id in invoked:
            if isinstance(validator_id, str) and validator_id:
                _phase_stats(stats, validator_id, phase)
        malformed = False
        for result in results:
            if not isinstance(result, dict):
                malformed = True
                continue
            validator_id = result.get("validator_id")
            severity = result.get("severity")
            if (
                not isinstance(validator_id, str)
                or not validator_id
                or severity not in SEVERITIES
            ):
                malformed = True
                continue
            bucket = _phase_stats(stats, validator_id, phase)
            if result.get("reused") is True:
                bucket.reused_severity_counts[severity] += 1
            else:
                bucket.severity_counts[severity] += 1
        if malformed:
            stats.skipped_events += 1
    return stats


def _phase_stats(
    stats: ValidatorOutcomeStats, validator_id: str, phase: str
) -> ValidatorPhaseStats:
    return stats.validators.setdefault(validator_id, {}).setdefault(
        phase, ValidatorPhaseStats()
    )


def _event_parts(event: Any) -> tuple[Any, Any, Any]:
    if isinstance(event, dict):
        return event.get("event_type"), event.get("data"), event.get("timestamp")
    return (
        getattr(event, "event_type", None),
        getattr(event, "data", None),
        getattr(event, "timestamp", None),
    )


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
