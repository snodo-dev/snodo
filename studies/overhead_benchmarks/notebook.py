"""Overhead Benchmarks study — governance latency (Task 8.3).

Measures real snodo component latency: token issue/verify,
policy evaluate, protocol verify, audit (hash-only + full-append),
checkpoint write/read, and end-to-end governed vs ungoverned.

Determinism note: timing is NOT bit-reproducible (OS noise, disk).
The study asserts stable op set + iteration counts + CSV schema,
not identical numbers.  This is documented and by design.

Headless: marimo run studies/overhead_benchmarks/notebook.py
Interactive: marimo edit studies/overhead_benchmarks/notebook.py
"""

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_studies_root = _here.parent
_project_root = _studies_root.parent
for _path in [str(_studies_root), str(_project_root)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

import marimo  # noqa: E402

__generated_with = "0.9.0"
app = marimo.App(width="medium")


# ============================================================
# Single import cell — all libraries imported once
# ============================================================

@app.cell
def __():
    import time
    import json as _json
    import statistics
    import tempfile
    import shutil
    import hashlib

    import numpy as _np
    import matplotlib.pyplot as _plt
    import matplotlib.patches as _mpatches
    import pandas as _pd

    from _common import apply_paper_style, save_figure, save_data

    from snodo.engine.policy import PolicyEvaluator, PolicyAction
    from snodo.compiler.models import (
        DisagreementPolicy, Protocol, Mode, Validator,
    )
    from snodo.core.interfaces import ValidatorResult, Task
    from snodo.infrastructure.tokens import TokenIssuer
    from snodo.compiler.verifier import verify_protocol
    from snodo.infrastructure.audit import AuditLog
    from snodo.infrastructure.session import SessionManager
    from snodo.engine.loop import build_protocol_graph
    from snodo.coders import MockAdapter

    apply_paper_style()

    return (
        _json, _np, _pd, _plt, _mpatches,
        apply_paper_style, hashlib, save_data, save_figure,
        shutil, statistics, tempfile, time,
        AuditLog, build_protocol_graph, DisagreementPolicy,
        MockAdapter, Mode, PolicyAction, PolicyEvaluator,
        Protocol, SessionManager, Task, TokenIssuer, Validator,
        ValidatorResult, verify_protocol,
    )


# ============================================================
# Helper — timing harness
# ============================================================

@app.cell
def __(statistics, time):
    def bench(name, fn, n_iterations=1000, setup=None):
        if setup:
            setup()
        times = []
        for _i in range(n_iterations):
            t0 = time.perf_counter()
            fn()
            t1 = time.perf_counter()
            times.append(t1 - t0)
        mean_s = statistics.mean(times)
        std_s = statistics.stdev(times) if len(times) > 1 else 0.0
        p50 = sorted(times)[len(times) // 2]
        p99 = sorted(times)[int(len(times) * 0.99)]
        _class = "cpu" if name in (
            "token_issue", "token_verify", "policy_evaluate",
            "protocol_verify", "audit_hash",
        ) else "disk"
        return {
            "op": name,
            "mean_us": mean_s * 1e6,
            "stddev_us": std_s * 1e6,
            "p50_us": p50 * 1e6,
            "p99_us": p99 * 1e6,
            "n_iterations": n_iterations,
            "class": _class,
        }
    return bench,


# ============================================================
# Warm imports + test fixtures
# ============================================================

@app.cell
def __(PolicyEvaluator, TokenIssuer):
    """Warm all heavy imports (litellm ~100ms+) before timing."""
    _ = PolicyEvaluator()
    _ = TokenIssuer()
    return


@app.cell
def __(Mode, Protocol, Validator, DisagreementPolicy):
    """Build test fixtures for microbenchmarks."""
    MODES = [Mode(mode_id="producer", name="Producer", tools=["edit"],
                   validators=["sec"])]
    VALS = [Validator(validator_id="sec", validator_type="security",
                       evaluation_phase="pre_execute", criteria=["check"])]
    TEST_PROTOCOL = Protocol(
        protocol_id="test", name="Test",
        modes=MODES, validators=VALS,
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    return MODES, TEST_PROTOCOL, VALS


@app.cell
def __(Mode, Protocol, Validator, DisagreementPolicy):
    """Minimal protocol for Part B — NO post_execute validators."""
    MIN_PROTOCOL = Protocol(
        protocol_id="bench", name="Bench",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                     validators=["sec"])],
        validators=[Validator(validator_id="sec", validator_type="security",
                               evaluation_phase="pre_execute",
                               criteria=["check"])],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    return MIN_PROTOCOL,


# ============================================================
# Part A — Per-operation microbenchmarks
# ============================================================

@app.cell
def __(TokenIssuer, ValidatorResult, PolicyEvaluator, DisagreementPolicy,
        verify_protocol, TEST_PROTOCOL, AuditLog, _json, hashlib,
        SessionManager, tempfile, bench):
    """Define, warm, and run Part A benchmark operations."""
    from pathlib import Path as _Plib

    _issuer = TokenIssuer()

    def _do_token_issue():
        _issuer.issue_token("bench", [
            ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
            ValidatorResult(validator_id="v2", severity="pass", justification="ok"),
        ])

    _token = _issuer.issue_token("bench", [
        ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
    ])
    def _do_token_verify():
        _issuer.verify_token(_token, expected_task_id="bench")

    _eval = PolicyEvaluator()
    _res = [
        ValidatorResult(validator_id="v1", severity="pass", justification="ok"),
        ValidatorResult(validator_id="v2", severity="pass", justification="ok"),
        ValidatorResult(validator_id="v3", severity="pass", justification="ok"),
    ]
    def _do_policy_evaluate():
        _eval.evaluate(_res, DisagreementPolicy.UNANIMOUS)

    def _do_protocol_verify():
        verify_protocol(TEST_PROTOCOL)

    _audit = AuditLog()
    for _i in range(3):
        _audit.append_event("test", {"op": "test", "val": _i})
    def _do_audit_hash():
        hashlib.sha256(_json.dumps(
            [e.__dict__ for e in _audit.events],
            sort_keys=True, default=str,
        ).encode()).hexdigest()

    def _do_audit_full():
        import os as _os2
        _p = ".snodo/.bench_audit_tmp.log"
        try:
            _os2.remove(_p)
        except FileNotFoundError:
            pass
        _af = AuditLog(log_path=_p)
        _af.append_event("test", {"op": "test", "val": 42})

    _tmp_sess = tempfile.mkdtemp(prefix="snodo_bench_")
    _smgr = SessionManager(sessions_dir=_Plib(_tmp_sess))

    def _do_checkpoint_write():
        _s = _smgr.create_session("producer", "/tmp/bench_proj")
        _smgr.save_checkpoint(_s.session_id)

    _s_chk = _smgr.create_session("producer", "/tmp/bench_proj")
    _smgr.save_checkpoint(_s_chk.session_id)
    def _do_checkpoint_read():
        _smgr.load_session(_s_chk.session_id)

    for _op in [
        _do_token_issue, _do_token_verify, _do_policy_evaluate,
        _do_protocol_verify, _do_audit_hash, _do_audit_full,
        _do_checkpoint_write, _do_checkpoint_read,
    ]:
        _op()

    results = []
    results.append(bench("token_issue", _do_token_issue, n_iterations=5000))
    results.append(bench("token_verify", _do_token_verify, n_iterations=5000))
    results.append(bench("policy_evaluate", _do_policy_evaluate, n_iterations=10000))
    results.append(bench("protocol_verify", _do_protocol_verify, n_iterations=500))
    results.append(bench("audit_hash", _do_audit_hash, n_iterations=1000))
    results.append(bench("audit_full_append", _do_audit_full, n_iterations=500))
    results.append(bench("checkpoint_write", _do_checkpoint_write, n_iterations=100))
    results.append(bench("checkpoint_read", _do_checkpoint_read, n_iterations=200))

    for _r in results:
        print(f"  {_r['op']:<22} mean={_r['mean_us']:8.1f} µs  "
              f"p50={_r['p50_us']:8.1f} µs  p99={_r['p99_us']:9.1f} µs  "
              f"n={_r['n_iterations']}")
    return results,


# ============================================================
# Part B — End-to-end governed vs ungoverned
# ============================================================

@app.cell
def __(time, statistics, tempfile, shutil, MIN_PROTOCOL, build_protocol_graph,
        MockAdapter, ValidatorResult):
    """Measure governed vs ungoverned wall time.

    Uses an all-pass validator_fn so the full governed path
    (governance → validate → execute → post_validate → complete)
    is benchmarked — not a pre_execute ESCALATE into blocked.
    """

    def _all_pass(task, validators, shell_mcp, current_mode=""):
        return [ValidatorResult(validator_id=v.validator_id, severity="pass",
                                justification="ok") for v in validators]

    N_E2E = 10
    GOVERNED = []
    UNGOVERNED = []

    for _i in range(N_E2E):
        _tmp = tempfile.mkdtemp()
        import subprocess
        subprocess.run(["git", "init"], cwd=_tmp, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=_tmp, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=_tmp, capture_output=True)
        try:
            g = build_protocol_graph(
                MIN_PROTOCOL, project_root=_tmp, use_mock_coder=True,
                validator_fn=_all_pass,
            )
            c = g.compile()

            t0 = time.perf_counter()
            c.invoke({
                "task": {"id": "e2e", "spec": "benchmark task"},
                "current_mode": "producer", "iteration": 0,
                "stage": "governance", "validation_results": [],
                "validation_token": None, "artifacts": [],
                "constraints_passed": True, "constraint_violations": [],
                "policy_decision": None, "is_complete": False,
                "is_blocked": False, "metadata": {}, "messages": [],
            })
            GOVERNED.append(time.perf_counter() - t0)

            coder = MockAdapter()
            t0 = time.perf_counter()
            coder.implement("benchmark task")
            UNGOVERNED.append(time.perf_counter() - t0)
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)

    gov_mean = statistics.mean(GOVERNED)
    ungov_mean = statistics.mean(UNGOVERNED)
    overhead_ms = (gov_mean - ungov_mean) * 1000

    LLM_LATENCY_S = 3.0
    overhead_pct = overhead_ms / (LLM_LATENCY_S * 1000) * 100

    print(f"\nGoverned ({len(GOVERNED)} runs):  {gov_mean*1000:.2f} ms")
    print(f"Ungoverned ({len(UNGOVERNED)} runs): {ungov_mean*1000:.2f} ms")
    print(f"Overhead: {overhead_ms:.2f} ms  ({overhead_pct:.2f}% of {LLM_LATENCY_S}s LLM)")
    return GOVERNED, LLM_LATENCY_S, UNGOVERNED, gov_mean, overhead_ms, overhead_pct, ungov_mean


# ============================================================
# CSV + Figures
# ============================================================

@app.cell
def __(results, gov_mean, ungov_mean, overhead_ms, overhead_pct, LLM_LATENCY_S, _pd):
    """Build DataFrame from benchmark results."""
    rows = []
    for r in results:
        rows.append({
            **r,
            "governed_mean_ms": gov_mean * 1000,
            "ungoverned_mean_ms": ungov_mean * 1000,
            "overhead_ms": overhead_ms,
            "llm_latency_assumption_s": LLM_LATENCY_S,
            "overhead_pct": overhead_pct,
        })
    bench_df = _pd.DataFrame(rows)
    print(bench_df[["op", "mean_us", "class"]].to_string(index=False))
    return bench_df,


@app.cell
def __(bench_df, save_data):
    csv_path = save_data(bench_df, "overhead_benchmarks")
    print(f"Data: {csv_path}")
    return csv_path,


@app.cell
def __(apply_paper_style, bench_df, _plt, _mpatches):
    """Primary figure: per-op latency bar chart (log scale)."""
    apply_paper_style()

    ops = bench_df["op"].tolist()
    means = bench_df["mean_us"].tolist()
    classes = bench_df["class"].tolist()
    cpu_col = "#0072B2"
    disk_col = "#D55E00"
    colors = [cpu_col if c == "cpu" else disk_col for c in classes]

    fig_latency, ax_latency = _plt.subplots(figsize=(7, 4.5))
    bars = ax_latency.barh(ops, means, color=colors)
    ax_latency.set_xscale("log")
    ax_latency.set(xlabel="mean latency (us, log scale)",
                   title="Per-operation governance latency")

    from matplotlib.patches import Patch
    ax_latency.legend(handles=[
        Patch(facecolor=cpu_col, label="CPU-bound (portable)"),
        Patch(facecolor=disk_col, label="Disk-bound (env-dependent)"),
    ], loc="lower right", frameon=False, fontsize=8)
    ax_latency.grid(True, alpha=0.3, axis="x")
    for bar, mean in zip(bars, means):
        ax_latency.text(bar.get_width() * 1.05,
                        bar.get_y() + bar.get_height() * 0.5,
                        f"{mean:.1f}", va="center", fontsize=7)
    _plt.tight_layout()
    return fig_latency,


@app.cell
def __(fig_latency, save_figure):
    svg1 = save_figure(fig_latency, "overhead_latency")
    print(f"Figure: {svg1}")
    return svg1,


@app.cell
def __(overhead_ms, overhead_pct, gov_mean, ungov_mean,
        LLM_LATENCY_S, apply_paper_style, _plt):
    """Secondary figure: governance overhead vs LLM latency."""
    apply_paper_style()
    fig_overhead, ax_overhead = _plt.subplots(figsize=(5.5, 2.0))

    g_s = gov_mean
    u_s = ungov_mean
    ax_overhead.barh(["Execution"], [g_s], color="#cccccc")
    ax_overhead.barh(["Execution"], [u_s], color="#0072B2",
                     label=f"Mock coder ({u_s*1000:.1f} ms)")
    ax_overhead.text(g_s * 1.02, 0,
                     f"+{overhead_ms:.2f} ms governance\n"
                     f"({overhead_pct:.2f}% of {LLM_LATENCY_S}s LLM)",
                     va="center", fontsize=7)
    ax_overhead.set(xlabel="seconds",
                    title="Governance overhead vs baseline")
    ax_overhead.legend(loc="lower right", frameon=False, fontsize=8)
    _plt.tight_layout()
    return fig_overhead,


@app.cell
def __(fig_overhead, save_figure):
    svg2 = save_figure(fig_overhead, "overhead_vs_llm")
    print(f"Figure: {svg2}")
    return svg2,


@app.cell
def __():
    import marimo as mo
    return mo,


if __name__ == "__main__":
    app.run()
