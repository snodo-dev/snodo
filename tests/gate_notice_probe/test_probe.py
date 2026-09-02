"""Reduced-gate wiring probe (Refs #87).

See ``__init__.py``. The plain test runs everywhere; the e2e test is what the
default marker filter deselects locally and what CI runs. Both assert trivially
— their purpose is to exist, with and without the ``e2e`` marker, for the wiring
test in ``tests/test_verification_gate.py``.
"""

import pytest


def test_probe_plain():
    assert True


@pytest.mark.e2e
def test_probe_e2e():
    assert True
