"""Tests for snodo models --benchmark.

FILE: tests/cli/test_models_benchmark.py

The benchmark makes a real, billed API call, so two things must be true before
anything else: it is unreachable unless ``--benchmark`` is passed, and its
prompt is the one stored in the repository rather than a string this module
assembles. Both are pinned here.
"""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import typer
from typer.testing import CliRunner

from snodo.cli.commands import models_cmd
from snodo.cli.commands.models_cmd import (
    _benchmark_prompt_identity,
    _load_benchmark_prompt,
    _run_benchmark_call,
    models_benchmark_command,
    models_command,
    register,
)


# ---------------------------------------------------------------------------
# Fakes for a streamed completion
# ---------------------------------------------------------------------------

class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    """One streamed chunk; ``usage`` appears only on the final chunk."""

    def __init__(self, content=None, usage=None):
        self.choices = [_Choice(content)] if content is not None else []
        self.usage = usage


class _Usage:
    def __init__(self, prompt_tokens=None, completion_tokens=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def _fake_completion(chunks):
    def _fn(**kwargs):
        return iter(chunks)
    return _fn


# ---------------------------------------------------------------------------
# 1. The benchmark path is explicit only
# ---------------------------------------------------------------------------

def test_benchmark_absent_when_not_asked(monkeypatch, capsys):
    """A plain listing never enters the benchmark path and never calls a model."""
    monkeypatch.setattr(
        "snodo.config.ConfigManager.get_providers",
        lambda self: {"openai": SimpleNamespace(api_key="sk-test")},
    )
    monkeypatch.setattr(
        models_cmd, "_get_models",
        lambda p, pc, force_refresh: [
            {"id": "gpt-4o", "full_string": "openai/gpt-4o", "context_window": 128000},
        ],
    )

    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("benchmark ran without being asked")

    monkeypatch.setattr(models_cmd, "_run_benchmark_call", _boom)

    args = SimpleNamespace(
        provider="openai", flush=False, stats=False, benchmark=False,
        id_contains=None, max_output_cost=None, min_output_cost=None,
        max_input_cost=None, min_context=None,
    )
    assert models_command(args) == 0
    assert called["n"] == 0
    assert "Benchmark" not in capsys.readouterr().out


def test_stats_does_not_reach_benchmark(monkeypatch, capsys):
    """--stats keeps its own path; it must not spill into a billed call."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("benchmark ran from --stats")

    monkeypatch.setattr(models_cmd, "_run_benchmark_call", _boom)
    monkeypatch.setattr(models_cmd, "models_stats_command", lambda args: 0)

    args = SimpleNamespace(stats=True, benchmark=False, provider=None)
    assert models_command(args) == 0
    assert called["n"] == 0


def test_benchmark_flag_reaches_benchmark_command(monkeypatch):
    """--benchmark dispatches to the benchmark command, not to listing."""
    seen = {"args": None}

    def _capture(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(models_cmd, "models_benchmark_command", _capture)

    args = SimpleNamespace(stats=False, benchmark=True, provider="openai")
    assert models_command(args) == 0
    assert seen["args"] is args


def test_cli_runner_benchmark_requires_provider(monkeypatch):
    """`snodo models --benchmark` without a provider is refused, not silently run."""
    app = typer.Typer()
    register(app)
    res = CliRunner().invoke(app, ["--benchmark"])
    assert res.exit_code == 0
    assert "--benchmark requires --provider" in res.output


def test_benchmark_requires_a_single_model(monkeypatch, capsys):
    """More than one model match is refused rather than an arbitrary pick."""
    monkeypatch.setattr(
        "snodo.config.ConfigManager.get_providers",
        lambda self: {"openai": SimpleNamespace(api_key="sk-test")},
    )
    monkeypatch.setattr(
        models_cmd, "_get_models",
        lambda p, pc, force_refresh: [
            {"id": "gpt-4o", "full_string": "openai/gpt-4o"},
            {"id": "gpt-4o-mini", "full_string": "openai/gpt-4o-mini"},
        ],
    )
    monkeypatch.setattr(
        models_cmd, "_run_benchmark_call",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")),
    )

    args = SimpleNamespace(
        provider="openai", flush=False, benchmark=True,
        id_contains=None, max_output_cost=None, min_output_cost=None,
        max_input_cost=None, min_context=None,
    )
    assert models_benchmark_command(args) == 1
    err = capsys.readouterr().err
    assert "exactly one model" in err


# ---------------------------------------------------------------------------
# 2. The prompt is read from the repository, not constructed at runtime
# ---------------------------------------------------------------------------

def test_prompt_is_read_from_the_repository():
    """The loaded prompt is the file's content and the file lives in the repo."""
    path = models_cmd._BENCHMARK_PROMPT_PATH
    assert path.is_file(), f"benchmark prompt missing at {path}"

    raw = path.read_text(encoding="utf-8")
    prompt = _load_benchmark_prompt()

    assert prompt in raw
    assert "FIFO queue" in prompt
    assert prompt.strip()


def test_prompt_is_not_a_runtime_string_literal():
    """The module must not carry the prompt as a literal it could change freely.

    A benchmark whose prompt is a string assembled in code is one refactor away
    from measuring a different prompt. The prompt's text is deliberately absent
    from the module source; only the file holds it.
    """
    source = Path(models_cmd.__file__).read_text(encoding="utf-8")
    prompt = _load_benchmark_prompt()
    assert prompt not in source
    # The requirements block is the giveaway phrase a hand-built prompt would use.
    assert "FifoQueue with enqueue" not in source


def test_changing_the_file_changes_the_loaded_prompt(tmp_path, monkeypatch):
    """Pointing at a different file yields its content unchanged (a real read)."""
    alt = tmp_path / "prompt.txt"
    alt.write_text("Only a repository file could produce this text.", encoding="utf-8")
    monkeypatch.setattr(models_cmd, "_BENCHMARK_PROMPT_PATH", alt)
    assert _load_benchmark_prompt() == "Only a repository file could produce this text."


def test_prompt_identity_names_the_exact_prompt():
    """Identity states the opening line, the length and a content hash."""
    prompt = _load_benchmark_prompt()
    ident = _benchmark_prompt_identity(prompt)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    assert "FIFO queue" in ident
    assert f"{len(prompt)} chars" in ident
    assert digest in ident


# ---------------------------------------------------------------------------
# 3. Measurement: token basis, timing, two rates
# ---------------------------------------------------------------------------

def test_run_benchmark_call_uses_provider_counts_when_reported():
    """Provider-reported usage is preferred, and the basis says so."""
    chunks = [_Chunk("def "), _Chunk("fifo"), _Chunk("(): pass\n"),
              _Chunk(usage=_Usage(prompt_tokens=11, completion_tokens=3))]
    result = _run_benchmark_call(
        "openai/gpt-4o",
        "Write a FIFO queue.",
        completion_fn=_fake_completion(chunks),
        token_counter_fn=lambda **k: 999,
    )
    assert result["output_tokens"] == 3
    assert result["prompt_tokens"] == 11
    assert result["counts_basis"] == "provider-reported usage"


def test_run_benchmark_call_falls_back_and_says_so():
    """With no provider usage, a local tokenizer stands in and the basis is named."""
    chunks = [_Chunk("a"), _Chunk("b"), _Chunk("c")]
    result = _run_benchmark_call(
        "openai/gpt-4o",
        "Write a FIFO queue.",
        completion_fn=_fake_completion(chunks),
        token_counter_fn=lambda **k: 7,
    )
    assert result["output_tokens"] == 7
    assert result["prompt_tokens"] == 7
    assert result["counts_basis"] == "local tokenizer estimate (provider reported no usage)"


def test_run_benchmark_call_reports_both_rates_and_ttft():
    """Time to first token and two throughput figures are all present."""
    chunks = [_Chunk("x"), _Chunk("y"), _Chunk("z"),
              _Chunk(usage=_Usage(prompt_tokens=5, completion_tokens=3))]
    result = _run_benchmark_call(
        "openai/gpt-4o",
        "Write a FIFO queue.",
        completion_fn=_fake_completion(chunks),
        token_counter_fn=lambda **k: 3,
    )
    assert result["time_to_first_token"] is not None
    assert result["wall_seconds"] > 0
    assert result["overall_tok_per_sec"] is not None
    # decode rate can only be computed once the first token is out and more
    # than one token followed; three tokens satisfy that.
    assert result["decode_tok_per_sec"] is not None


def test_report_states_prompt_counts_and_timing_basis(monkeypatch, capsys):
    """The report a reader sees carries prompt identity, counts basis and timing."""
    monkeypatch.setattr(
        "snodo.config.ConfigManager.get_providers",
        lambda self: {"openai": SimpleNamespace(api_key="sk-test")},
    )
    monkeypatch.setattr(
        models_cmd, "_get_models",
        lambda p, pc, force_refresh: [
            {"id": "gpt-4o", "full_string": "openai/gpt-4o"},
        ],
    )

    def _fake_call(model, prompt, *a, **k):
        return {
            "output_tokens": 42,
            "prompt_tokens": 12,
            "counts_basis": "provider-reported usage",
            "time_to_first_token": 0.25,
            "wall_seconds": 1.5,
            "decode_tok_per_sec": 33.6,
            "overall_tok_per_sec": 28.0,
        }

    monkeypatch.setattr(models_cmd, "_run_benchmark_call", _fake_call)

    args = SimpleNamespace(
        provider="openai", flush=False, benchmark=True,
        id_contains=None, max_output_cost=None, min_output_cost=None,
        max_input_cost=None, min_context=None,
    )
    assert models_benchmark_command(args) == 0
    out = capsys.readouterr().out
    assert "openai/gpt-4o" in out
    assert "FIFO queue" in out               # prompt identity
    assert "prompt 12 / output 42" in out    # the counts divided
    assert "provider-reported usage" in out  # ... and whose counts they are
    assert "first token 0.25s" in out
    assert "total wall 1.50s" in out
    assert "decode 33.6 output tok/s" in out
    assert "overall 28.0 output tok/s" in out


def test_benchmark_intent_says_it_will_spend(monkeypatch, capsys):
    """Before the call, the command states the model, the prompt and the cost."""
    monkeypatch.setattr(
        "snodo.config.ConfigManager.get_providers",
        lambda self: {"openai": SimpleNamespace(api_key="sk-test")},
    )
    monkeypatch.setattr(
        models_cmd, "_get_models",
        lambda p, pc, force_refresh: [
            {"id": "gpt-4o", "full_string": "openai/gpt-4o"},
        ],
    )

    def _fail(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(models_cmd, "_run_benchmark_call", _fail)

    args = SimpleNamespace(
        provider="openai", flush=False, benchmark=True,
        id_contains=None, max_output_cost=None, min_output_cost=None,
        max_input_cost=None, min_context=None,
    )
    assert models_benchmark_command(args) == 1
    captured = capsys.readouterr()
    assert "real, billed API call" in captured.out
    assert "openai/gpt-4o" in captured.out
    assert "FIFO queue" in captured.out
    assert "Benchmark call failed" in captured.err
