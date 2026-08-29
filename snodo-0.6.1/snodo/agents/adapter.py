"""Agent adapter layer - backward compatibility re-exports.

FILE: snodo/agents/adapter.py

All adapter classes now live in snodo.coders.
This module re-exports them for backward compatibility.
"""

from snodo.coders import (  # noqa: F401
    LiteLLMAdapter,
    MockAdapter,
    BasicCoderAdapter,
    MockCoderAdapter,
    create_coder,
    AdapterError,
    LLMCallError,
    ParseError,
)
