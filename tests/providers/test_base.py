"""Tests for CodeHostProvider ABC.

FILE: tests/providers/test_base.py
"""

import pytest

from snodo.providers.base import CodeHostProvider, ProviderError


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
            def create_pr(self, branch, title, body):
                return "url"

            def read_pr_diff(self, pr_number):
                return "diff"

            def post_review_comment(self, pr_number, comment):
                return "ok"

            def approve_pr(self, pr_number):
                return "approved"

            def reject_pr(self, pr_number, reason):
                return "rejected"

            def merge_pr(self, pr_number):
                return "merged"

            def read_pr_comments(self, pr_number):
                return "{}"

        provider = StubProvider()
        assert provider.create_pr("b", "t", "d") == "url"
        assert provider.read_pr_diff(1) == "diff"
        assert provider.approve_pr(1) == "approved"

    def test_partial_implementation_raises(self):
        """Missing abstract methods prevent instantiation."""

        class PartialProvider(CodeHostProvider):
            def create_pr(self, branch, title, body):
                return "url"

        with pytest.raises(TypeError):
            PartialProvider()
