"""Adversarial Validator Sensitivity (Task 8.5).

Characterizes how the four disagreement policies tolerate validators
that deviate from correct behaviour.  Imports the REAL
PolicyEvaluator; feeds it result sets where f of N validators are
adversarial.

Two directions:
  Compromised-permissive (always-pass): collision/bypass risk
  Compromised-restrictive (always-block): DoS risk

Terminology: framed as "adversarial validator sensitivity," NOT
Byzantine Fault Tolerance.  Snodo's policies are aggregation rules,
not a consensus protocol.

Headless: marimo run studies/byzantine_robustness/notebook.py
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
    import pandas as pd
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
    return (SEED, apply_paper_style, gen, np, pd, plt, rng,
            save_data, save_figure,
            PolicyEvaluator, PolicyAction, DisagreementPolicy, ValidatorResult,
            ALL_POLICIES, POLICY_NAMES)


@app.cell
def __(gen, np, pd, plt, apply_paper_style, save_figure, save_data,
        PolicyEvaluator, PolicyAction, DisagreementPolicy, ValidatorResult,
        ALL_POLICIES, POLICY_NAMES):
    """
    Adversarial validator sensitivity

    N = 5 total validators.  f are adversarial (two directions):
      Compromised-permissive: f validators always emit "pass"
      Compromised-restrictive: f validators always emit "blocker"

    The remaining N-f honest validators detect with accuracy p.
    Error model (from 8.2):
      BAD task: correct = "blocker", error = 50% pass / 50% warn
      GOOD task: correct = "pass", error = 60% warn / 40% blocker

    The real PolicyEvaluator is imported and called directly.
    Blocker→HALT (INV3) overrides policy; a single always-block
    validator should halt every policy (the design cost).

    Connects to 8.4: adversarial always-X is the rho→1 limit
    made deliberate — one validator's vote deterministically
    overrides all others.

    Frame: adversarial validator sensitivity, NOT Byzantine
    Fault Tolerance.  Snodo's policies are aggregation rules,
    not a consensus protocol.
    """

    N = 5
    p = 0.7          # honest-validator accuracy
    NUM_TRIALS = 20000

    evaluator = PolicyEvaluator(quorum_threshold=0.67)

    # ============================================================
    # Direction 1 — always-pass (compromised-permissive)
    # ============================================================
    # BAD task should be refused.  Adversarial validators emit
    # "pass" regardless.  Honest validators detect with accuracy p.
    # False-pass: POUTCOME = PROCEED | task is BAD.

    pass_risks = {}  # policy -> list of (f, false_pass_rate)

    for pol in ALL_POLICIES:
        _k = pol.value
        pass_risks[_k] = []
        for f_val in range(N + 1):
            _bad = f_val       # adversarial always-pass
            _hon = N - _bad   # honest validators

            _proceed = 0
            for _ in range(NUM_TRIALS):
                _results = []
                # adversarial: always pass
                for _i in range(_bad):
                    _results.append(ValidatorResult(
                        validator_id=f"adv{_i}", severity="pass",
                        justification="adversarial always-pass"))

                # honest: detect with accuracy p on bad task
                for _i in range(_hon):
                    if gen.random() < p:
                        sev = "blocker"  # correct → detect defect
                    else:
                        sev = "pass" if gen.random() < 0.5 else "warn"
                    _results.append(ValidatorResult(
                        validator_id=f"hon{_i}", severity=sev,
                        justification=""))

                _dec = evaluator.evaluate(_results, pol)
                if _dec.action in (PolicyAction.PROCEED, PolicyAction.PROCEED_WITH_LOG):
                    _proceed += 1

            pass_risks[_k].append({
                "f": f_val, "direction": "always_pass",
                "policy": _k, "false_pass_rate": _proceed / NUM_TRIALS,
            })

    # ============================================================
    # Direction 2 — always-block (compromised-restrictive)
    # ============================================================
    # GOOD task should proceed.  Adversarial validators emit
    # "blocker" regardless.  Honest validators approve with
    # accuracy p.  False-block: POUTCOME = HALT or ESCALATE | task is GOOD.
    # INV3 prediction: any f≥1 always-block → HALT for all policies.

    block_risks = {}

    for pol in ALL_POLICIES:
        _k = pol.value
        block_risks[_k] = []
        for f_val in range(N + 1):
            _bad = f_val
            _hon = N - _bad

            _blocked = 0
            for _ in range(NUM_TRIALS):
                _results = []
                # adversarial: always block
                for _i in range(_bad):
                    _results.append(ValidatorResult(
                        validator_id=f"adv{_i}", severity="blocker",
                        justification="adversarial always-block"))

                # honest: detect with accuracy p on good task
                for _i in range(_hon):
                    if gen.random() < p:
                        sev = "pass"
                    else:
                        sev = "warn" if gen.random() < 0.6 else "blocker"
                    _results.append(ValidatorResult(
                        validator_id=f"hon{_i}", severity=sev,
                        justification=""))

                _dec = evaluator.evaluate(_results, pol)
                if _dec.action in (PolicyAction.HALT, PolicyAction.ESCALATE):
                    _blocked += 1

            block_risks[_k].append({
                "f": f_val, "direction": "always_block",
                "policy": _k, "false_block_rate": _blocked / NUM_TRIALS,
            })

    # ============================================================
    # Print results
    # ============================================================
    print("=== Always-pass (compromised-permissive) tolerance ===")
    for pol in ALL_POLICIES:
        _k = pol.value
        _rates = [r["false_pass_rate"] for r in pass_risks[_k]]
        _first_broken = next((f for f in range(N+1) if _rates[f] > 0.01), N+1)
        print(f"  {POLICY_NAMES[pol]:<10}  first-broken at f={_first_broken}  "
              f"f=1={_rates[1]:.4f}  f=N={_rates[N]:.4f}")

    print("\n=== Always-block (compromised-restrictive) tolerance ===")
    for pol in ALL_POLICIES:
        _k = pol.value
        _rates = [r["false_block_rate"] for r in block_risks[_k]]
        _first_broken = next((f for f in range(N+1) if _rates[f] > 0.01), N+1)
        print(f"  {POLICY_NAMES[pol]:<10}  first-broken at f={_first_broken}  "
              f"f=1={_rates[1]:.4f}  f=N={_rates[N]:.4f}")

    # INV3 verification: f=1 always-block → ALL policies 100% blocked?
    _inv3_ok = all(block_risks[pol.value][1]["false_block_rate"] > 0.99
                   for pol in ALL_POLICIES)
    print(f"\nINV3 verification: f=1 always-block halts all policies? {'YES' if _inv3_ok else 'NO — investigate'}")

    # ============================================================
    # CSV
    # ============================================================
    rows_csv = []
    for _pol in ALL_POLICIES:
        _k = _pol.value
        for i, (pr, br) in enumerate(zip(pass_risks[_k], block_risks[_k])):
            rows_csv.append({**pr, "false_block_rate": br["false_block_rate"]})

    bench = pd.DataFrame(rows_csv)
    csv_path = save_data(bench, "byzantine_robustness")
    print(f"\nData: {csv_path}  ({len(bench)} rows)")

    # ============================================================
    # Figure — two-panel: always-pass (left) + always-block (right)
    # ============================================================
    apply_paper_style()
    fig, (ax_pass, ax_block) = plt.subplots(1, 2, figsize=(9, 4.0))

    _markers = ["o", "s", "D", "^"]
    _styles = ["-", "--", "-.", ":"]
    f_vals = list(range(N + 1))

    for i, pol in enumerate(ALL_POLICIES):
        _k = pol.value
        _fp = [r["false_pass_rate"] for r in pass_risks[_k]]
        _fb = [r["false_block_rate"] for r in block_risks[_k]]
        ax_pass.plot(f_vals, _fp, marker=_markers[i], linestyle=_styles[i],
                     linewidth=1.5, markersize=4, label=POLICY_NAMES[pol])
        ax_block.plot(f_vals, _fb, marker=_markers[i], linestyle=_styles[i],
                      linewidth=1.5, markersize=4, label=POLICY_NAMES[pol])

    ax_pass.set(xlabel="f (adversarial always-pass validators)",
                ylabel="False-pass rate", title="Always-pass (collusion/bypass)")
    ax_pass.legend(fontsize=7, frameon=False)
    ax_pass.grid(True, alpha=0.3)

    ax_block.set(xlabel="f (adversarial always-block validators)",
                 ylabel="False-block rate", title="Always-block (DoS)")
    ax_block.legend(fontsize=7, frameon=False)
    ax_block.grid(True, alpha=0.3)

    # Annotate the INV3 asymmetry
    ax_block.annotate("INV3: f=1 halts all policies",
                      xy=(1, 1.0), xytext=(2.5, 0.92),
                      arrowprops=dict(arrowstyle="->", color="black"),
                      fontsize=8, color="black")

    fig.suptitle("Adversarial validator sensitivity (N=5, p=0.7)", fontsize=11)
    plt.tight_layout()
    svg_path = save_figure(fig, "byzantine_tolerance")
    print(f"Figure: {svg_path}")

    return bench, csv_path, svg_path


@app.cell
def __():
    import marimo as mo
    return mo,


if __name__ == "__main__":
    app.run()
