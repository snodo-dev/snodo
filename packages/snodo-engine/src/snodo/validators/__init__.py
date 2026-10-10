"""Validator package — initialize the registry and built-ins on import.

Import the registry as the package's sole initializer dependency. The registry
creates its singleton before importing built-in validators, preserving the
historical ``import snodo.validators`` registration behavior without having
the package initializer import validators ahead of registry initialization.
"""

from snodo.validators import registry as _registry  # noqa: F401
