"""Validator package with the historical built-in import surface.

The registry owns initialization order: it creates its singleton before loading
built-ins, then discovers third-party entry points.  Importing it here keeps
``import snodo.validators`` equivalent to the former eager package API while
also making direct ``snodo.validators.registry`` imports safe.
"""

from importlib import import_module

__all__ = [
    "AcceptanceValidator",
    "LLMValidator",
    "ProtocolAdherenceValidator",
    "QualityValidator",
    "ValidatorBase",
    "ValidatorContext",
]


def __getattr__(name: str):
    """Resolve the long-standing package-level validator exports lazily.

    Laziness matters when ``registry`` is the first imported submodule: eagerly
    importing a built-in here would re-enter the registry before its singleton
    has been constructed.
    """
    if name in {"ValidatorBase", "ValidatorContext"}:
        module = import_module("snodo.validators.context")
    elif name in __all__:
        module_name = {
            "AcceptanceValidator": "acceptance",
            "LLMValidator": "llm_validator",
            "ProtocolAdherenceValidator": "protocol_adherence",
            "QualityValidator": "quality",
        }[name]
        module = import_module(f"snodo.validators.{module_name}")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(module, name)


# Importing the package retains eager registry initialization; if this module
# is being initialized as part of a registry-first import, import_module returns
# that in-progress module and the registry continues after package setup.
import_module("snodo.validators.registry")
