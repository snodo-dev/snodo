"""Report coder settings the selected coder cannot honour.

FILE: snodo/coders/inert_settings.py

The ``llm.coder`` section is presented as one set of settings for "the
coder", but the coders are not alike: an in-process LiteLLM coder runs its
own tool loop, so ``max_tool_turns`` and ``max_tokens`` govern it, while a
subprocess coder spawns a program that runs its own loop, where only the
model and ``timeout_seconds`` arrive. Nothing in the wiring says which is
which, so a setting an operator relies on can be doing nothing, and the only
way to find out is to watch it have no effect.

The unknown-key check (ADR 046, #298) closes the typo path to inertness: an
UNKNOWN key now fails loudly. A known key that does not apply to the
selected coder is the same fault one level deeper, and harder to see because
the key is real — an operator told to raise ``llm.coder.max_tool_turns``
after a halted ``opencode-cli`` run will change a number and re-run into the
same wall.

The remedy is declaration, not transcription: each adapter declares the
settings it actually reads as ``honoured_settings`` — a value stored on the
adapter and never read is NOT honoured, which is exactly the fact only the
adapter can know — and where a coder is selected, every setting the operator
explicitly wrote is checked against that declaration. Each inert pairing is
reported by name, the setting's and the coder's, at selection time.

It is reported, never raised: an operator switching coders must not have
their config rejected over a setting that was real for the coder they left.
And a setting left at its default is no one's expectation, so it stays
silent — only what was written is weighed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Type

from snodo.coders.base import CoderAdapter
from snodo.infrastructure.config import CoderConfig

_logger = logging.getLogger(__name__)

#: The ``llm.coder`` fields weighed at the coder surface. Derived from the
#: model so a future coder setting is weighed without editing a transcription
#: here. ``concurrency`` is deliberately absent: it is the dispatcher's
#: capacity ceiling (honoured by ``snodo plan run``'s wave dispatch), not a
#: knob any coder reads — it never reaches an adapter, so no coder can be
#: faulted for not honouring it.
JUDGED_CONFIG_FIELDS: tuple[str, ...] = tuple(
    field for field in CoderConfig.model_fields if field != "concurrency"
)

#: Capabilities the engine injects into every coder unconditionally (#68);
#: they are plumbing the engine negotiates, not settings an operator relies on
#: the coder to honour, so they are never weighed even when present in a
#: protocol's ``coder_config``.
_ENGINE_INJECTED: frozenset[str] = frozenset({"workspace_mcp", "progress_callback"})


def explicit_coder_settings(
    coder_cfg: CoderConfig,
    mode_coder_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the coder settings the operator actually wrote, keyed by name.

    A setting left at its default was relied on by no one and must not be
    mentioned; ``model_fields_set`` is what distinguishes the value the
    operator wrote from the value pydantic filled in. Every key of a
    protocol's ``mode.coder_config`` is explicit by construction — it was
    written down — including settings like ``temperature`` that reach the
    adapter only through that escape hatch.
    """
    explicit: Dict[str, Any] = {
        field: getattr(coder_cfg, field)
        for field in JUDGED_CONFIG_FIELDS
        if field in coder_cfg.model_fields_set
    }
    for key, value in (mode_coder_config or {}).items():
        if key in _ENGINE_INJECTED:
            continue
        explicit[key] = value
    return explicit


def inert_settings(
    adapter_cls: Type[CoderAdapter],
    explicit: Mapping[str, Any],
) -> List[str]:
    """The explicit settings ``adapter_cls`` cannot honour, sorted by name.

    The adapter's own ``honoured_settings`` declaration is the source of
    truth: from the outside, a value that arrives and is read is
    indistinguishable from one that is stored and never read, but the adapter
    knows. An adapter that declares nothing is UNKNOWN, and unknown must stay
    silent — falsely accusing a real setting is worse than missing one; the
    conformance suite makes an undeclared coder a test failure rather than an
    operator blind spot.
    """
    honoured = getattr(adapter_cls, "honoured_settings", None)
    if honoured is None:
        return []
    return sorted(key for key in explicit if key not in honoured)


def report_inert_coder_settings(
    coder_name: str,
    explicit: Mapping[str, Any],
) -> List[str]:
    """Warn for each explicit setting the selected coder cannot honour.

    Called where a coder is selected — graph build and the governance-driven
    respawn — so the operator hears "this coder cannot honour that setting"
    at the moment the pairing is made, not after a halted run is being
    explained. Returns the reported setting names. Never raises: the setting
    is real and correct for another coder, and rejecting the config of an
    operator who just switched coders would be its own fault.
    """
    from snodo.coders import CODER_REGISTRY

    adapter_cls = CODER_REGISTRY.get(coder_name)
    if adapter_cls is None:
        # Unknown coder names are ``get_coder``'s to reject, loudly.
        return []
    inert = inert_settings(adapter_cls, explicit)
    honoured = sorted(getattr(adapter_cls, "honoured_settings", frozenset()))
    for key in inert:
        source = (
            f"llm.coder.{key}"
            if key in JUDGED_CONFIG_FIELDS
            else f"coder_config.{key}"
        )
        _logger.warning(
            "Coder '%s' cannot honour the explicitly configured setting "
            "'%s' (=%r): this coder does not read it, so it will have no "
            "effect. Settings '%s' honours: %s.",
            coder_name,
            source,
            explicit[key],
            coder_name,
            ", ".join(honoured) or "none",
        )
    return inert
