"""A plain test with no e2e sibling (Refs #257).

See ``__init__.py``. Scoping pytest to this path collects one non-e2e test, so
the default marker filter deselects nothing and the reduced-gate banner must not
print.
"""


def test_probe_plain_only():
    assert True
