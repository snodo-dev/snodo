"""Unit tests for pytest collection and rootdir guards in tests/conftest.py."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import pytest_configure, pytest_collection_modifyitems, _suite_repo_root


def test_pytest_configure_rootdir_mismatch_raises_usage_error():
    fake_config = SimpleNamespace(rootpath=Path("/tmp/wrong/path"), rootdir="/tmp/wrong/path")
    repo_root = _suite_repo_root()
    if repo_root is None:
        return

    with pytest.raises(pytest.UsageError, match="pytest rootdir mismatch"):
        pytest_configure(fake_config)


def test_pytest_configure_rootdir_matches_succeeds():
    repo_root = _suite_repo_root()
    if repo_root is None:
        return

    fake_config = SimpleNamespace(rootpath=repo_root, rootdir=str(repo_root))
    # Should not raise
    pytest_configure(fake_config)


def test_pytest_collection_modifyitems_under_collection_raises_usage_error():
    fake_config = SimpleNamespace(
        args=["tests/"],
        rootdir="/fake/repo",
        getoption=lambda opt, default: None,
    )
    fake_items = [SimpleNamespace()] * 450  # 450 items < 2000

    with pytest.raises(pytest.UsageError, match="Under-collection detected"):
        pytest_collection_modifyitems(None, fake_config, fake_items)


def test_pytest_collection_modifyitems_subpath_target_bypasses_min_count():
    fake_config = SimpleNamespace(
        args=["tests/cli"],
        rootdir="/fake/repo",
        getoption=lambda opt, default: None,
    )
    fake_items = [SimpleNamespace()] * 450

    # Targeting a specific sub-path ('tests/cli') should not raise
    pytest_collection_modifyitems(None, fake_config, fake_items)


def test_pytest_collection_modifyitems_full_suite_valid_count_succeeds():
    fake_config = SimpleNamespace(
        args=["tests/"],
        rootdir="/fake/repo",
        getoption=lambda opt, default: None,
    )
    fake_items = [SimpleNamespace()] * 2500

    # 2500 items >= 2000 should not raise
    pytest_collection_modifyitems(None, fake_config, fake_items)
