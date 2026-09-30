"""Mocked API tests for the GitLab code-host plugin."""

import json
from importlib.metadata import entry_points
from unittest.mock import MagicMock, patch

import pytest

from snodo.providers.base import ProviderError
from snodo_provider_gitlab import GitLabProvider


@pytest.fixture
def provider():
    with patch("snodo_provider_gitlab.gitlab") as client_module:
        client = client_module.Gitlab.return_value
        project = client.projects.get.return_value
        project.default_branch = "main"
        instance = GitLabProvider(metadata={"gitlab_repo": "group/project"}, token="token")
        instance._mock_project = project
        yield instance


def test_entry_point_resolves_provider():
    point = next(ep for ep in entry_points(group="snodo.providers") if ep.name == "gitlab")
    assert point.load() is GitLabProvider


@pytest.mark.parametrize("url", [
    "git@gitlab.com:group/project.git",
    "https://gitlab.com/group/project.git",
])
def test_claims_gitlab_remotes(url):
    assert GitLabProvider.claims_remote(url)


def test_does_not_claim_github_remote():
    assert not GitLabProvider.claims_remote("git@github.com:group/project.git")


def test_create_mr_uses_default_or_explicit_target(provider):
    provider._mock_project.mergerequests.create.return_value.iid = 42
    assert provider.create_change_request("feature", "title", "body") == "42"
    provider._mock_project.mergerequests.create.assert_called_once_with({
        "source_branch": "feature", "target_branch": "main", "title": "title", "description": "body",
    })
    provider.create_change_request("branch", "title", "body", "release")
    assert provider._mock_project.mergerequests.create.call_args.args[0]["target_branch"] == "release"


def test_diff_is_rendered(provider):
    mr = provider._mock_project.mergerequests.get.return_value
    mr.changes.return_value = {"changes": [{"old_path": "a", "new_path": "a", "diff": "+ok"}]}
    assert "diff --git a/a b/a\n+ok" == provider.read_change_request_diff("42")
    provider._mock_project.mergerequests.get.assert_called_with(42)


def test_comment_approval_changes_and_merge(provider):
    mr = provider._mock_project.mergerequests.get.return_value
    mr.discussions.create.return_value.id = "note-1"
    mr.merge.return_value = {"sha": "123456789abcdef"}
    assert provider.post_change_request_comment("42", "hello") == "note-1"
    assert provider.approve_change_request("42").endswith("approved")
    assert provider.request_change_request_changes("42", "fix this").endswith("changes requested")
    assert "12345678" in provider.merge_change_request("42")
    assert mr.approve.called and mr.unapprove.called and mr.merge.called
    assert mr.discussions.create.call_count == 2


def test_discussion_returns_contract_json(provider):
    mr = provider._mock_project.mergerequests.get.return_value
    mr.title = "Change"
    mr.discussions.list.return_value = [MagicMock(attributes={"notes": [
        {"author": {"username": "alice"}, "body": "hello", "system": False},
    ]})]
    payload = json.loads(provider.read_change_request_discussion("42"))
    assert payload == {"title": "Change", "comments": [{"author": "alice", "body": "hello"}], "reviews": []}


def test_missing_token_names_environment_variable():
    with patch.dict("os.environ", {}, clear=True), patch("snodo_provider_gitlab.GitLabProvider._get_git_remote", create=True):
        with pytest.raises(ProviderError, match="GITLAB_TOKEN"):
            GitLabProvider(metadata={"gitlab_repo": "group/project"}, token="")
