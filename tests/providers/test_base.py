"""Tests for CodeHostProvider ABC.

FILE: tests/providers/test_base.py
"""

import pytest
from snodo.providers.base import CodeHostProvider, ProviderError
from snodo.providers import CODE_HOST_PROVIDER_INTERFACE_VERSION


class TestCodeHostProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError, match="abstract"):
            CodeHostProvider()

    def test_provider_error_is_exception(self):
        assert issubclass(ProviderError, Exception)

    def test_provider_error_message(self):
        err = ProviderError("test error")
        assert str(err) == "test error"

    def test_concrete_implementation(self):
        """A concrete provider implementing all methods can be instantiated."""

        class StubProvider(CodeHostProvider):
            def create_change_request(self, branch, title, body, target_branch=None):
                return "url"

            def read_change_request_diff(self, change_request_id):
                return "diff"

            def post_change_request_comment(self, change_request_id, comment):
                return "ok"

            def approve_change_request(self, change_request_id):
                return "approved"

            def request_change_request_changes(self, change_request_id, reason):
                return "rejected"

            def merge_change_request(self, change_request_id):
                return "merged"

            def read_change_request_discussion(self, change_request_id):
                return "{}"

        provider = StubProvider()
        assert provider.create_change_request("b", "t", "d", "develop") == "url"
        assert provider.read_change_request_diff("opaque/id") == "diff"
        assert provider.approve_change_request("opaque/id") == "approved"

    def test_legacy_methods_delegate_with_string_ids(self):
        class StubProvider(CodeHostProvider):
            def create_change_request(self, branch, title, body, target_branch=None):
                return f"create:{branch}:{target_branch}"

            def read_change_request_diff(self, change_request_id):
                return change_request_id

            def post_change_request_comment(self, change_request_id, comment):
                return f"{change_request_id}:{comment}"

            def approve_change_request(self, change_request_id):
                return change_request_id

            def request_change_request_changes(self, change_request_id, reason):
                return f"{change_request_id}:{reason}"

            def merge_change_request(self, change_request_id):
                return change_request_id

            def read_change_request_discussion(self, change_request_id):
                return change_request_id

        provider = StubProvider()
        assert provider.create_pr("b", "t", "d") == "create:b:None"
        assert provider.read_pr_diff(7) == "7"
        assert provider.post_review_comment(7, "x") == "7:x"
        assert provider.approve_pr(7) == "7"
        assert provider.reject_pr(7, "x") == "7:x"
        assert provider.merge_pr(7) == "7"
        assert provider.read_pr_comments(7) == "7"
        assert CODE_HOST_PROVIDER_INTERFACE_VERSION == 1

    def test_partial_implementation_raises(self):
        """Missing abstract methods prevent instantiation."""

        class PartialProvider(CodeHostProvider):
            pass

        with pytest.raises(TypeError):
            PartialProvider()
