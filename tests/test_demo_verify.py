"""Tests for optional demo patch verification."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_verify", ROOT / "demo" / "verify.py"
)
demo_verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo_verify)


INSTANCE = {"instance_id": "scikit-learn__scikit-learn-26194"}


def test_skipped_when_docker_missing(monkeypatch):
    monkeypatch.setattr(demo_verify.shutil, "which", lambda _: None)

    result = demo_verify.verify_patch(INSTANCE, "patch", lambda *_: {"resolved": True})

    assert result == {"status": "skipped", "reason": "Docker not found"}


def test_skipped_when_scorer_raises(monkeypatch):
    monkeypatch.setattr(demo_verify.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        "experiments.scoring.get_instance",
        lambda *_: {"instance_id": INSTANCE["instance_id"]},
    )

    def fail(*_):
        raise RuntimeError("harness unavailable")

    result = demo_verify.verify_patch(INSTANCE, "patch", fail)

    assert result == {
        "status": "skipped",
        "reason": "verification failed: harness unavailable",
    }


def test_empty_patch_is_unresolved():
    result = demo_verify.verify_patch(INSTANCE, "")

    assert result == {"status": "unresolved", "reason": "no patch"}


def test_resolved_comes_from_scorer_result(monkeypatch):
    monkeypatch.setattr(demo_verify.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        "experiments.scoring.get_instance",
        lambda *_: {"instance_id": INSTANCE["instance_id"]},
    )

    result = demo_verify.verify_patch(INSTANCE, "patch", lambda *_: {"resolved": True})

    assert result == {"status": "resolved", "reason": "fail-to-pass tests passed"}


def test_unresolved_comes_from_scorer_result(monkeypatch):
    monkeypatch.setattr(demo_verify.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        "experiments.scoring.get_instance",
        lambda *_: {"instance_id": INSTANCE["instance_id"]},
    )

    result = demo_verify.verify_patch(INSTANCE, "patch", lambda *_: {"resolved": False})

    assert result == {"status": "unresolved", "reason": "fail-to-pass tests failed"}
