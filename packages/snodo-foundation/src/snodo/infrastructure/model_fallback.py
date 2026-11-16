"""Operational failover across an ordered list of model names."""

from collections.abc import Callable, Iterable
from typing import TypeVar

from snodo.infrastructure.provider_errors import is_transient_provider_error

T = TypeVar("T")


def call_with_model_fallback(
    models: Iterable[str],
    call: Callable[[str], T],
    *,
    on_switch: Callable[[str, str, str], None] | None = None,
) -> tuple[T, str]:
    """Call each distinct model at most once, advancing only on operational faults.

    Returns the call result and the model that produced it. ``on_switch`` receives
    the failed model, the next model, and a concise reason for the transition.
    """
    unique_models = list(dict.fromkeys(models))
    if not unique_models:
        raise ValueError("at least one model is required")

    for index, model in enumerate(unique_models):
        try:
            return call(model), model
        except Exception as error:
            if not is_transient_provider_error(error) or index == len(unique_models) - 1:
                raise
            next_model = unique_models[index + 1]
            if on_switch is not None:
                on_switch(model, next_model, str(error))
    raise AssertionError("unreachable")
