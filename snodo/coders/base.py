"""Base coder adapter interface and exceptions.

FILE: snodo/coders/base.py

Defines the CoderAdapter ABC that all coder backends implement.
"""

from snodo.core.interfaces import Coder


# CoderAdapter is the canonical name for the coder interface.
# It's an alias for the core Coder ABC to provide a clearer name
# in the adapter context while maintaining interface compatibility.
CoderAdapter = Coder


class AdapterError(Exception):
    """Base exception for adapter operations."""


class LLMCallError(AdapterError):
    """LLM API call failed."""


class ParseError(AdapterError):
    """Failed to parse LLM output."""
