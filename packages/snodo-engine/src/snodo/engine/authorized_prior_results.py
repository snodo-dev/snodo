"""Pure lookup for authorized prior validator warns on unchanged specs."""

from collections.abc import Iterable, Mapping
from typing import Any

from snodo.core.spec import same_spec
from snodo.core.task_lineage import same_task_lineage


def authorized_prior_results(
    task_id: str,
    current_root_spec: str,
    validator_ids: Iterable[str],
    issuer: Any,
    decision_records: Iterable[str],
    prior_results: Iterable[Mapping[str, Any]],
    prior_spec: str,
) -> dict[str, Mapping[str, Any]]:
    """Return prior warn results that have a valid proceed decision and spec.

    ``issuer`` verifies decision JWTs through ``find_adjudicated``; no decision
    payload or prior-result record is trusted based on unverified convenience
    fields. The stored result and spec are the existing persisted shapes.
    """
    if not same_spec(current_root_spec, prior_spec):
        return {}

    results_by_validator = {
        result.get("validator_id"): result
        for result in prior_results
        if isinstance(result, Mapping) and result.get("severity") == "warn"
    }
    records = list(decision_records)
    authorized: dict[str, Mapping[str, Any]] = {}
    for validator_id in validator_ids:
        result = results_by_validator.get(validator_id)
        if result is None:
            continue
        if (
            issuer.find_adjudicated(
                records,
                task_id,
                validator_id,
                "warn",
                task_matcher=same_task_lineage,
            )
            is not None
        ):
            authorized[validator_id] = result
    return authorized
