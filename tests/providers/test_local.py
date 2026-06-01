"""Tests for LocalProvider.

FILE: tests/providers/test_local.py
"""

import pytest

from snodo.providers.base import ProviderError
from snodo.providers.local import LocalProvider


@pytest.fixture
def local_provider():
    return LocalProvider()


class TestLocalProvider:
    def test_create_pr_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.create_pr("branch", "title", "body")

    def test_read_pr_diff_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.read_pr_diff(42)

    def test_post_review_comment_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.post_review_comment(42, "comment")

    def test_approve_pr_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.approve_pr(42)

    def test_reject_pr_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.reject_pr(42, "reason")

    def test_merge_pr_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.merge_pr(42)

    def test_read_pr_comments_raises(self, local_provider):
        with pytest.raises(ProviderError, match="no remote code host"):
            local_provider.read_pr_comments(42)

    def test_is_code_host_provider(self, local_provider):
        from snodo.providers.base import CodeHostProvider
        assert isinstance(local_provider, CodeHostProvider)
