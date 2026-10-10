"""Provider-facing tool results remain text, including JSON schema documents."""

from snodo.coders.tool_result_text import provider_tool_result_text


def test_json_schema_tool_result_is_not_parsed_as_gemini_structured_response():
    from litellm.litellm_core_utils.prompt_templates.factory import (
        convert_to_gemini_tool_call_result,
    )

    schema = '{"$defs":{"Severity":{"type":"string"}},"$ref":"#/$defs/Severity"}'
    message = {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": provider_tool_result_text(schema),
    }
    previous = {"tool_calls": [{"id": "call_1", "function": {"name": "read_file"}}]}

    transformed = convert_to_gemini_tool_call_result(message, previous)

    assert transformed["function_response"]["response"] == {
        "content": f"Tool result (text):\n{schema}"
    }


def test_plain_text_tool_result_is_unchanged():
    assert provider_tool_result_text("file contents\nsecond line") == (
        "file contents\nsecond line"
    )
