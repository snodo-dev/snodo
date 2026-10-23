"""Formatting helpers for provider-facing tool results."""


MAX_TOOL_RESULT_CHARS = 100_000


def bound_tool_result(result: str, *, preserve_tail: bool = False) -> str:
    """Bound the text sent to the model, noting the exact omitted character count."""
    if len(result) <= MAX_TOOL_RESULT_CHARS:
        return result

    notice_template = "\n\n[TRUNCATED: {omitted} characters omitted. Read the log selectively or run a narrower test target.]"
    notice = notice_template.format(omitted=len(result))
    body_budget = MAX_TOOL_RESULT_CHARS - len(notice)
    head_size = body_budget // 2 if preserve_tail else body_budget
    tail_size = body_budget - head_size if preserve_tail else 0
    omitted = len(result) - head_size - tail_size
    notice = notice_template.format(omitted=omitted)
    body_budget = MAX_TOOL_RESULT_CHARS - len(notice)
    head_size = body_budget // 2 if preserve_tail else body_budget
    tail_size = body_budget - head_size if preserve_tail else 0
    return result[:head_size] + (result[-tail_size:] if tail_size else "") + notice


def provider_tool_result_text(result: str) -> str:
    """Keep JSON-shaped results as text for providers that parse JSON content."""
    if result.lstrip().startswith(("{", "[")):
        return f"Tool result (text):\n{result}"
    return result
