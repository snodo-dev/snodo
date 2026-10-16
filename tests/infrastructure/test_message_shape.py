from snodo.infrastructure.message_shape import (
    is_message_shape_rejection,
    message_shape_adapted_models,
    reshape_messages_after_rejection,
)


class _BadRequest(Exception):
    status_code = 400


ERROR = (
    "CloudflareException - AiError: Bad input: Error: oneOf at '/' not met, "
    "0 matches: required properties at '/' are 'prompt', Type mismatch of "
    "'/messages/0/content', 'array' not in 'string', required properties at "
    "'/messages/1' are 'role,content' … code 5006"
)


def test_recognizes_strict_message_shape_rejection():
    assert is_message_shape_rejection(_BadRequest(ERROR))
    assert not is_message_shape_rejection(_BadRequest("invalid model name"))
    assert not is_message_shape_rejection(RuntimeError(ERROR))


def test_reshapes_text_parts_and_contentless_tool_call_message():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}]},
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
    ]
    reshaped = reshape_messages_after_rejection(_BadRequest(ERROR), messages, "shape-test", set())
    assert reshaped == [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "tool_calls": [{"id": "c1"}], "content": ""},
    ]
    assert messages[0]["content"][0]["text"] == "hello"


def test_non_text_content_is_preserved():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
    result = reshape_messages_after_rejection(
        _BadRequest(ERROR), [{"role": "user", "content": [image]}], "image-test", set()
    )
    assert result == [{"role": "user", "content": [image]}]


def test_remembers_models_and_attempts_once_per_run():
    attempted: set[str] = set()
    name = "memory-test-model"
    assert name not in message_shape_adapted_models()
    assert reshape_messages_after_rejection(_BadRequest(ERROR), [], name, attempted) == []
    assert name in message_shape_adapted_models()
    assert reshape_messages_after_rejection(_BadRequest(ERROR), [], name, attempted) is None
