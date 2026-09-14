"""Scoped gate-probe package with no e2e test (Refs #257).

The existing ``tests/gate_notice_probe`` package holds one plain and one e2e
test, so a scoped run over it always has something to reduce. This package holds
only plain tests: a scoped run over it deselects nothing, which is the case the
banner must stay silent for. It lives under the default suite like every other
probe, so it costs one trivial test and stays honest about the real wiring.
"""
