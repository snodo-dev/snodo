"""Validation tests for the benchmark provider/model shorthand."""

from types import SimpleNamespace

import pytest

from snodo.cli.commands.models_benchmark_name import resolve_benchmark_name


@pytest.mark.parametrize(
    ("value", "expected"),
    [("openai/gpt-4o", ("openai", "gpt-4o")),
     ("router/vendor/model", ("router", "vendor/model"))],
)
def test_resolve_benchmark_name(value, expected):
    args = SimpleNamespace(benchmark_model=value, provider=None, id=None)
    assert resolve_benchmark_name(args) == expected
    assert args.id == expected[1]


@pytest.mark.parametrize("value", ["", "noslash", "/model", "provider/"])
def test_resolve_benchmark_name_rejects_malformed(value):
    args = SimpleNamespace(benchmark_model=value, provider=None, id=None)
    with pytest.raises(ValueError, match="benchmark model"):
        resolve_benchmark_name(args)


@pytest.mark.parametrize(
    ("provider", "model_id"), [("openai", None), (None, "gpt-4o")]
)
def test_resolve_benchmark_name_rejects_conflicting_flags(provider, model_id):
    args = SimpleNamespace(
        benchmark_model="openai/gpt-4o", provider=provider, id=model_id,
    )
    with pytest.raises(ValueError, match="Use --benchmark"):
        resolve_benchmark_name(args)
