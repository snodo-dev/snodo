"""Failure-reason survival tests for the coder adapters.

FILE: tests/coders/test_failure_reason_survival.py

Each test induces a failure on a debugging path (git anchor, provider
response shape, model tool-call parsing, container probing) and asserts
the failure's type and message reach the log where an operator would
look — not merely that the safe value was returned.
"""

import logging
from types import SimpleNamespace

from snodo.coders.base import InPlaceCoderAdapter
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.opencode_container import OpenCodeContainer


class _ProbeInPlaceAdapter(InPlaceCoderAdapter):
    """Minimal concrete in-place adapter, only to reach the base-class paths."""

    coder_name = "probe"

    def _implement_in_place(self, spec):  # pragma: no cover - never called
        raise NotImplementedError


def _joined(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


class TestRecordHeadBeforeRun:
    """coders/base.py: a missing HEAD anchor must say why it is missing."""

    def test_head_read_failure_logs_type_and_message(self, tmp_path, caplog):
        adapter = _ProbeInPlaceAdapter()
        adapter._workspace = tmp_path  # a plain directory, not a git repo

        with caplog.at_level(logging.WARNING, logger="snodo.coders.base"):
            head = adapter._record_head_before_run()

        assert head is None  # safe value keeps being returned
        # Not a crash, but also not a blank: the git failure's type and
        # message are on the record.
        assert "InvalidGitRepositoryError" in _joined(caplog)
        assert "Could not record HEAD" in _joined(caplog)


class TestCoderSubmitFilesParsing:
    """litellm coder: a dropped delivery must leave its reason behind."""

    def test_unparseable_submit_files_args_logs_reason(self, caplog):
        tc = SimpleNamespace(
            function=SimpleNamespace(
                name="submit_files",
                arguments='{"files": [{"path": ',  # truncated mid-argument
            )
        )
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.litellm"):
            files = LiteLLMAdapter._extract_submit_files(tc)

        assert files is None
        assert "submit_files arguments unparseable" in _joined(caplog)
        assert "JSONDecodeError" in _joined(caplog)

    def test_unexpected_response_shape_does_not_silently_pass_truncation_check(self, caplog):
        adapter = LiteLLMAdapter()
        # choices is empty: the truncation check cannot run, and the caller
        # must not be able to read that as "checked, not truncated".
        response = SimpleNamespace(choices=[])
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.litellm"):
            adapter._check_truncation(response)

        assert "Truncation check skipped" in _joined(caplog)
        assert "IndexError" in _joined(caplog)

    def test_json_fallback_chain_logs_reason(self, caplog):
        broken = '```json\n[{"path": "a.py", \n```'
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.litellm"):
            parsed = LiteLLMAdapter._extract_json(broken)

        assert parsed is None
        assert "fenced-block" in _joined(caplog)
        assert "JSONDecodeError" in _joined(caplog)


class TestOpenCodeContainerProbes:
    """Availability probes: False means "not available", but only the log
    says whether the daemon is absent or the probe itself broke."""

    def test_ping_failure_logs_reason(self, caplog):
        def boom():
            raise ConnectionError("Cannot connect to the Docker daemon at tcp://dind:2375")

        container = OpenCodeContainer()
        container._client = SimpleNamespace(ping=boom)
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.opencode_container"):
            assert container.is_available() is False

        assert "Docker ping failed" in _joined(caplog)
        assert "Cannot connect to the Docker daemon" in _joined(caplog)

    def test_image_lookup_failure_logs_reason(self, caplog):
        def boom(name):
            raise RuntimeError("500 Server Error: page failed")

        container = OpenCodeContainer()
        container._client = SimpleNamespace(
            images=SimpleNamespace(get=boom)
        )
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.opencode_container"):
            assert container.image_exists() is False

        assert "lookup failed" in _joined(caplog)
        assert "500 Server Error" in _joined(caplog)

    def test_health_request_failure_logs_reason(self, caplog, monkeypatch):
        import httpx

        def boom(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", boom)
        container = OpenCodeContainer()
        container._container = SimpleNamespace(
            reload=lambda: None, status="running"
        )
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.opencode_container"):
            assert container._is_container_healthy() is False

        assert "health check request failed" in _joined(caplog)
        assert "connection refused" in _joined(caplog)

    def test_reload_failure_logs_reason(self, caplog):
        def boom():
            raise RuntimeError("Container 1234abcd not found")

        container = OpenCodeContainer()
        container._container = SimpleNamespace(reload=boom, status="running")
        with caplog.at_level(logging.DEBUG, logger="snodo.coders.opencode_container"):
            assert container.is_running() is False

        assert "Container reload failed" in _joined(caplog)
        assert "not found" in _joined(caplog)
