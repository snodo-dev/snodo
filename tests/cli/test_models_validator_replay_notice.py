"""Tests for the validator replay preflight notice."""

from snodo.cli.commands.models_benchmark_notice import print_validator_replay_notice


def test_notice_names_validator_phase_and_commit(capsys):
    print_validator_replay_notice(
        {"validator_results": [{"validator_id": "tests", "model": "old-model"}]},
        "tests",
        "post",
        "abc123",
        "new-model",
    )

    output = capsys.readouterr().err
    assert "validator tests" in output
    assert "phase post" in output
    assert "commit abc123" in output
    assert "candidate model: new-model" in output
    assert "billed or uses your subscription" in output


def test_notice_says_recorded_model_unknown_when_absent(capsys):
    print_validator_replay_notice({}, "tests", "post", "abc123", "new-model")

    assert "Recorded model: unknown" in capsys.readouterr().err


def test_notice_goes_to_stderr_only(capsys):
    print_validator_replay_notice({}, "tests", "post", "abc123", "new-model")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
