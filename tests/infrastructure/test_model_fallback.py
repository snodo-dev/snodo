import pytest

from snodo.infrastructure.model_fallback import call_with_model_fallback


def test_first_model_answers_without_switch():
    switches = []
    assert call_with_model_fallback(["a", "b"], lambda model: model, on_switch=lambda *x: switches.append(x)) == ("a", "a")
    assert switches == []


def test_operational_failure_switches_and_reports_answer():
    switches = []
    def call(model):
        if model == "a":
            raise RuntimeError("rate limit")
        return "answer"
    assert call_with_model_fallback(["a", "b"], call, on_switch=lambda *x: switches.append(x)) == ("answer", "b")
    assert switches == [("a", "b", "rate limit")]


def test_non_operational_error_stops():
    calls = []
    with pytest.raises(ValueError, match="bad"):
        call_with_model_fallback(["a", "b"], lambda model: (calls.append(model), (_ for _ in ()).throw(ValueError("bad")))[1])
    assert calls == ["a"]


def test_last_operational_error_is_raised_unchanged():
    first, last = RuntimeError("timed out"), RuntimeError("connection reset")
    def call(model):
        raise first if model == "a" else last
    with pytest.raises(RuntimeError) as caught:
        call_with_model_fallback(["a", "b"], call)
    assert caught.value is last


def test_duplicates_and_ordered_switch_callbacks():
    switches = []
    def call(model):
        if model != "c":
            raise RuntimeError("timed out")
        return 3
    assert call_with_model_fallback(["a", "a", "b", "c"], call, on_switch=lambda *x: switches.append(x)) == (3, "c")
    assert switches == [("a", "b", "timed out"), ("b", "c", "timed out")]


def test_single_model_is_plain_call():
    assert call_with_model_fallback(["only"], lambda model: model.upper()) == ("ONLY", "only")
