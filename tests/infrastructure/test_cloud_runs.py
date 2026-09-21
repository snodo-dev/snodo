"""Run-record cloud delivery is opt-in and lease-admitted."""

from unittest.mock import MagicMock, patch

import httpx

from snodo.infrastructure.cloud_lease import CloudLease
from snodo.infrastructure.cloud_runs import send_run_record


def _config():
    return {
        "cloud": {
            "sync_enabled": True,
            "api_key": "sndo_live_test",
            "api_url": "https://api.snodo.test/v1",
        },
    }


def test_no_lease_means_no_run_record_send():
    with patch("snodo.infrastructure.cloud_runs.get_admission_lease", return_value=None), \
            patch("snodo.infrastructure.cloud_runs.httpx.post") as post:
        assert not send_run_record({"task_id": "task-1", "completed_at": 2.0}, _config())
    post.assert_not_called()


def test_transient_failure_backs_off_and_retries_same_record():
    lease = CloudLease("lease-1", "opaque-token", 9_999_999_999.0)
    failed = MagicMock(spec=httpx.Response)
    failed.status_code = 503
    failed.headers = {}
    succeeded = MagicMock(spec=httpx.Response)
    succeeded.status_code = 204
    responses = [failed, succeeded]

    with patch("snodo.infrastructure.cloud_runs.get_admission_lease", return_value=lease), \
            patch("snodo.infrastructure.cloud_runs.httpx.post", side_effect=responses) as post, \
            patch("snodo.infrastructure.cloud_runs.time.sleep") as sleep:
        assert send_run_record({"task_id": "task-1", "completed_at": 2.0}, _config())

    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["content"] == post.call_args_list[1].kwargs["content"]
    sleep.assert_called_once()
    assert "/run-records/lease-1" in post.call_args_list[0].args[0]
