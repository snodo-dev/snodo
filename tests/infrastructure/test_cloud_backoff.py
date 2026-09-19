"""Shared cloud backoff policy tests."""

from snodo.infrastructure.cloud_backoff import cloud_backoff_seconds


def test_server_supplied_delay_is_honoured_without_exceeding_cap():
    assert cloud_backoff_seconds(3, 7.0) == 7.0
    assert cloud_backoff_seconds(3, 90.0) == 60.0


def test_repeated_failures_increase_the_delay():
    delays = [cloud_backoff_seconds(n, random_value=1.0) for n in range(1, 5)]
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_clients_refused_together_do_not_return_together():
    first = cloud_backoff_seconds(1, random_value=0.0)
    second = cloud_backoff_seconds(1, random_value=1.0)
    assert first != second
    assert first >= 1.0
    assert second >= 1.0
