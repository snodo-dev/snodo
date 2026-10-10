"""Formatting helpers for provider-facing tool results."""


def provider_tool_result_text(result: str) -> str:
    """Keep JSON-shaped results as text for providers that parse JSON content."""
    if result.lstrip().startswith(("{", "[")):
        return f"Tool result (text):\n{result}"
    return result
