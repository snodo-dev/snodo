"""Fixture package exercised by the reduced-gate wiring test (Refs #87).

Holds one plain test and one e2e test so tests/test_verification_gate.py can
run pytest over this path and assert the local run announces that its e2e test
was deselected (default), and stays silent when the marker filter is cleared
(``-m ""``) or selects only e2e (``-m e2e``).

It lives under a directory the default suite collects, so the e2e test here is
deselected exactly like the real e2e suite — the point is that the reduced gate
behaves the same way over a small, fast target as it does over the whole tree.
"""
