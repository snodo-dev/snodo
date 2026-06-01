"""Policy Monte Carlo study — false-block vs false-pass trade-off curves (Task 8.2).

Simulates validator accuracy across the four disagreement policies
(unanimous, majority, quorum, any) using the REAL PolicyEvaluator
from snodo.engine.policy (post-fix: thresholds on pass_count, warn
withholds approval).  Produces paper-ready SVG figures and CSV data.

Validator-error model:
  - N=3 validators (2+N reference protocol), each with accuracy p
  - Only real severities: pass, warn, blocker (no invented severities)
  - GOOD task (should proceed):
      correct (p)  → pass
      error  (1-p) → 60% warn (phantom concern), 40% blocker (false alarm)
  - BAD task (should be refused):
      correct (p)  → blocker (genuine defect)
      error  (1-p) → 50% pass (complete miss), 50% warn (partial miss)

  Post-fix result (policy.py thresholds on pass_count):
    - blocker_count > 0 → HALT always (unchanged)
    - pass_count checked against policy thresholds (unanimous=total,
      majority>half, quorum>=0.67*total, any>=1)
    - Policies now separate: unanimous strictest (highest false-block),
      any most permissive, majority/quorum in between
    - Real ESCALATE share on good tasks (not 100% HALT)

Refusal modes tracked separately: HALT vs ESCALATE.
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


@app.cell
def __():
    import numpy as np
    import matplotlib.pyplot as plt
    from _common import SEED, rng, apply_paper_style, save_figure, save_data
    from snodo.engine.policy import PolicyEvaluator, PolicyAction
    from snodo.compiler.models import DisagreementPolicy
    from snodo.core.interfaces import ValidatorResult

    apply_paper_style()
    gen = rng(SEED)

    ALL_POLICIES = [
        DisagreementPolicy.UNANIMOUS,
        DisagreementPolicy.MAJORITY,
        DisagreementPolicy.QUORUM,
        DisagreementPolicy.ANY,
    ]
    POLICY_NAMES = {
        DisagreementPolicy.UNANIMOUS: "Unanimous",
        DisagreementPolicy.MAJORITY: "Majority",
        DisagreementPolicy.QUORUM: "Quorum",
        DisagreementPolicy.ANY: "Any",
    }
    return (
        SEED,
        apply_paper_style,
        gen,
        np,
        rng,
        save_figure,
        save_data,
        plt,
        PolicyEvaluator,
        PolicyAction,
        DisagreementPolicy,
        ValidatorResult,
        ALL_POLICIES,
        POLICY_NAMES,
    )


@app.cell
def __(gen, np, ALL_POLICIES, PolicyEvaluator, PolicyAction, ValidatorResult):
    """
    Validator-error model (real severities only, post policy fix)

    N = 3 validators, accuracy p each.
    Only the three real severities the engine accepts: pass, warn, blocker.

    GOOD task (should proceed):
      correct → pass
      error   → 60% warn (phantom concern), 40% blocker (false alarm)

    BAD task (should be refused):
      correct → blocker (genuine defect)
      error   → 50% pass (complete miss), 50% warn (partial miss)

    PolicyEvaluator (post-fix): thresholds on pass_count; warn withholds
    approval.  blocker→HALT unchanged.  Policies now separate.
    HALT and ESCALATE tracked separately.
    """

    N = 3
    NUM_TRIALS = 25000  # higher count for cleaner sampling

    evaluator = PolicyEvaluator(quorum_threshold=0.67)

    def simulate_one(p, policy, ground_truth):
        halt = 0
        escalate = 0
        proceed = 0

        for _t in range(NUM_TRIALS):
            reslist = []
            for _vi in range(N):
                if gen.random() < p:
                    sev = "pass" if ground_truth == "good" else "blocker"
                else:
                    _r = gen.random()
                    if ground_truth == "good":
                        sev = "warn" if _r < 0.6 else "blocker"
                    else:
                        sev = "pass" if _r < 0.5 else "warn"
                reslist.append(ValidatorResult(
                    validator_id=f"v{_vi}", severity=sev, justification="",
                ))

            decision = evaluator.evaluate(reslist, policy)

            if decision.action == PolicyAction.HALT:
                halt += 1
            elif decision.action == PolicyAction.ESCALATE:
                escalate += 1
            else:
                proceed += 1

        halt_rate = halt / NUM_TRIALS
        escalate_rate = escalate / NUM_TRIALS
        total_refusal = halt_rate + escalate_rate
        proceed_rate = proceed / NUM_TRIALS

        false_pass = proceed_rate if ground_truth == "bad" else 0.0
        false_block = total_refusal if ground_truth == "good" else 0.0

        return {
            "halt_rate": halt_rate, "escalate_rate": escalate_rate,
            "total_refusal": total_refusal, "proceed_rate": proceed_rate,
            "false_pass": false_pass, "false_block": false_block,
        }

    p_values = np.linspace(0.5, 1.0, 21)
    return N, NUM_TRIALS, evaluator, p_values, simulate_one


@app.cell
def __(p_values, ALL_POLICIES, simulate_one, NUM_TRIALS):
    """Run the full Monte Carlo sweep."""
    import time

    sim_results = {}
    start = time.time()
    for _pol in ALL_POLICIES:
        _k = _pol.value
        sim_results[_k] = []
        for _p in p_values:
            good = simulate_one(_p, _pol, "good")
            bad = simulate_one(_p, _pol, "bad")
            sim_results[_k].append({"p": _p, "good": good, "bad": bad})
    elapsed = time.time() - start
    print(f"Monte Carlo sweep: {elapsed:.1f}s  "
          f"({len(ALL_POLICIES)} policies × {len(p_values)} p-points × {NUM_TRIALS} trials)")
    return elapsed, sim_results


@app.cell
def __(sim_results, ALL_POLICIES):
    """Build DataFrame from simulation results."""
    import pandas as pd

    rows = []
    for _pol in ALL_POLICIES:
        _k = _pol.value
        for _entry in sim_results[_k]:
            rows.append({
                "policy": _k, "accuracy_p": _entry["p"],
                "good_halt_rate": _entry["good"]["halt_rate"],
                "good_escalate_rate": _entry["good"]["escalate_rate"],
                "good_total_refusal": _entry["good"]["total_refusal"],
                "good_false_block": _entry["good"]["false_block"],
                "bad_halt_rate": _entry["bad"]["halt_rate"],
                "bad_escalate_rate": _entry["bad"]["escalate_rate"],
                "bad_total_refusal": _entry["bad"]["total_refusal"],
                "bad_proceed_rate": _entry["bad"]["proceed_rate"],
                "bad_false_pass": _entry["bad"]["false_pass"],
            })

    study_df = pd.DataFrame(rows)
    print(f"DataFrame: {len(study_df)} rows")
    return study_df


@app.cell
def __(study_df, save_data):
    """Save CSV to outputs/."""
    csv_path = save_data(study_df, "policy_monte_carlo")
    print(f"Data: {csv_path}")
    return csv_path


@app.cell
def __(apply_paper_style, study_df, ALL_POLICIES, POLICY_NAMES, plt):
    """Primary figure: false-block vs false-pass trade-off curves.

    Post policy-fix: policies now separate.  Unanimous is strictest
    (highest false-block), Any most permissive, Majority/Quorum in
    between.  At N=3, Quorum (>= 3×0.67 = 2.01 → needs 3 passes)
    mirrors Unanimous functionally; divergence would appear at N≥5.
    """

    apply_paper_style()
    _markers = ["o", "s", "D", "^"]
    _styles = ["-", "--", "-.", ":"]

    fig_tradeoff, ax_tradeoff = plt.subplots(figsize=(5.5, 4.5))

    for _i, _pol in enumerate(ALL_POLICIES):
        _k = _pol.value
        _sub = study_df[study_df["policy"] == _k]
        fp_vals = _sub["bad_false_pass"].values
        fb_vals = _sub["good_false_block"].values
        ax_tradeoff.plot(fp_vals, fb_vals, marker=_markers[_i], linestyle=_styles[_i],
                         label=POLICY_NAMES[_pol], linewidth=1.5, markersize=4,
                         markevery=2)

    ax_tradeoff.set(xlabel="False-pass rate\n(approving bad work)",
                    ylabel="False-block rate\n(refusing good work)",
                    title="Policy trade-off: false-block vs false-pass (N=3, real severities)")
    ax_tradeoff.legend(loc="upper right", frameon=False, fontsize=9)
    ax_tradeoff.grid(True, alpha=0.3)
    ax_tradeoff.set_xlim(left=-0.02)
    ax_tradeoff.set_ylim(bottom=-0.02)
    plt.tight_layout()
    return fig_tradeoff


@app.cell
def __(apply_paper_style, study_df, ALL_POLICIES, POLICY_NAMES, plt):
    """Secondary figure: refusal-mode breakdown per policy (GOOD tasks)."""

    apply_paper_style()
    fig_breakdown, axes_breakdown = plt.subplots(2, 2, figsize=(7, 5.5), sharex=True, sharey=True)
    axes_flat = axes_breakdown.flatten()

    for _i, _pol in enumerate(ALL_POLICIES):
        _ax = axes_flat[_i]
        _k = _pol.value
        _sub = study_df[study_df["policy"] == _k]
        p_vals = _sub["accuracy_p"].values
        halt_vals = _sub["good_halt_rate"].values
        esc_vals = _sub["good_escalate_rate"].values

        _ax.fill_between(p_vals, 0, esc_vals, alpha=0.5, label="ESCALATE", color="#D55E00")
        _ax.fill_between(p_vals, esc_vals, halt_vals, alpha=0.5, label="HALT", color="#0072B2")
        _ax.set_title(POLICY_NAMES[_pol], fontsize=10)
        _ax.set(ylabel="Refusal rate")
        if _i >= 2:
            _ax.set(xlabel="Validator accuracy p")
        _ax.grid(True, alpha=0.3)
        if _i == 0:
            _ax.legend(frameon=False, fontsize=8)

    fig_breakdown.suptitle("Refusal-mode breakdown (GOOD tasks, N=3, real severities)", fontsize=10)
    plt.tight_layout()
    return fig_breakdown


@app.cell
def __(fig_tradeoff, save_figure):
    """Save primary trade-off figure."""
    svg1 = save_figure(fig_tradeoff, "policy_tradeoff")
    print(f"Figure: {svg1}")
    return svg1


@app.cell
def __(fig_breakdown, save_figure):
    """Save secondary breakdown figure."""
    svg2 = save_figure(fig_breakdown, "policy_refusal_breakdown")
    print(f"Figure: {svg2}")
    return svg2


@app.cell
def __():
    import marimo as mo
    return mo,


if __name__ == "__main__":
    app.run()
