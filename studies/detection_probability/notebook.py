"""Failure-Rate Bounds Validation — Monte Carlo (Task 8.4).

Validates the paper's analytical closed-form equations for
correlation-aware quorum miss probability and
structural-vs-behavioral failure rate.  No engine imports.

Headless: marimo run studies/detection_probability/notebook.py
Interactive: marimo edit studies/detection_probability/notebook.py
"""

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_studies_root = _here.parent
if str(_studies_root) not in sys.path:
    sys.path.insert(0, str(_studies_root))

import marimo  # noqa: E402

__generated_with = "0.9.0"
app = marimo.App(width="medium")


@app.cell
def __():
    import numpy as np
    import matplotlib.pyplot as plt
    import pandas as pd
    from _common import SEED, rng, apply_paper_style, save_figure, save_data

    apply_paper_style()
    gen = rng(SEED)
    return SEED, apply_paper_style, gen, np, pd, plt, rng, save_data, save_figure


@app.cell
def __(apply_paper_style, gen, np, pd, plt, save_figure, save_data):
    # ============================================================
    # Paper parameters
    # ============================================================
    PV = 0.2        # per-validator miss rate
    RHO = 0.3       # intraclass correlation
    PREC = 0.1      # recovery loop failure
    ALPHA = 0.15    # cascading susceptibility
    N_STAGES = 5    # pipeline stages
    P_I = 0.1       # per-stage defect probability

    # ============================================================
    # Helper: latent-factor quorum miss simulation
    # ============================================================
    N_SIM = 100000

    def sim_quorum_miss(p_v, K, rho, n_sim=N_SIM):
        misses = 0
        for _ in range(n_sim):
            if gen.random() < rho:
                if gen.random() < p_v:
                    misses += 1
            else:
                if all(gen.random() < p_v for _ in range(K)):
                    misses += 1
        return misses / n_sim

    def formula_quorum_miss(p_v, K, rho):
        return p_v**K + rho * (p_v - p_v**K)

    def structural_failure_rate(p_i_list, p_v, K, rho, p_recovery):
        Pm = formula_quorum_miss(p_v, K, rho)
        return sum(pi * Pm + pi * (1 - Pm) * p_recovery for pi in p_i_list)

    def behavioral_failure_rate(p_i_list, alpha):
        pp = p_i_list[0]
        prod = 1 - pp
        for i in range(1, len(p_i_list)):
            pp = p_i_list[i] + alpha * pp * (1 - p_i_list[i])
            prod *= (1 - pp)
        return 1 - prod

    # ============================================================
    # Tier 1 — rho sweep (sim vs formula)
    # ============================================================
    rho_vals = np.linspace(0, 1, 21)
    rows_t1 = []
    for K in [3, 5]:
        for rh in rho_vals:
            rows_t1.append({
                "rho": rh, "K": K, "p_v": PV,
                "simulated": sim_quorum_miss(PV, K, rh),
                "analytical": formula_quorum_miss(PV, K, rh),
            })

    print("=== Tier 1: quorum miss formula validation ===")
    for K in [3, 5]:
        s0 = sim_quorum_miss(PV, K, 0.0)
        s1 = sim_quorum_miss(PV, K, 1.0)
        a0 = PV**K
        a1 = PV
        print(f"  K={K}: rho=0  sim={s0:.6f} formula={a0:.6f}  Δ={abs(s0-a0):.6f}")
        print(f"  K={K}: rho=1  sim={s1:.6f} formula={a1:.6f}  Δ={abs(s1-a1):.6f}")

    # ============================================================
    # Tier 2 baseline
    # ============================================================
    pis = [P_I] * N_STAGES
    Ps = structural_failure_rate(pis, PV, 3, RHO, PREC)
    Pb = behavioral_failure_rate(pis, ALPHA)
    ratio = Pb / Ps if Ps > 0 else float("inf")

    print("\n=== Tier 2: structural vs behavioral (baseline) ===")
    print(f"  P_structural = {Ps:.6f}  P_behavioral = {Pb:.6f}")
    print(f"  Ratio = {ratio:.2f}x")
    print(f"  (p_i={P_I}, p_v={PV}, K=3, rho={RHO}, p_recovery={PREC}, alpha={ALPHA}, N={N_STAGES})")

    # ============================================================
    # Tier 2 — N sweep
    # ============================================================
    N_vals = list(range(1, 21))
    rows_n = []
    for N in N_vals:
        pis_n = [P_I] * N
        rows_n.append({
            "N": N,
            "P_structural": structural_failure_rate(pis_n, PV, 3, RHO, PREC),
            "P_behavioral": behavioral_failure_rate(pis_n, ALPHA),
        })
    print(f"\nN=1:  P_s={rows_n[0]['P_structural']:.6f}  P_b={rows_n[0]['P_behavioral']:.6f}")
    print(f"N=10: P_s={rows_n[9]['P_structural']:.6f}  P_b={rows_n[9]['P_behavioral']:.6f}")
    print(f"N=20: P_s={rows_n[19]['P_structural']:.6f}  P_b={rows_n[19]['P_behavioral']:.6f}")

    # ============================================================
    # Tier 2 — alpha sweep
    # ============================================================
    alpha_vals = np.linspace(0, 0.5, 21)
    rows_a = []
    for a in alpha_vals:
        rows_a.append({
            "alpha": a,
            "P_structural": structural_failure_rate(pis, PV, 3, RHO, PREC),
            "P_behavioral": behavioral_failure_rate(pis, a),
        })
    print(f"\nalpha=0:     ratio={rows_a[0]['P_behavioral']/rows_a[0]['P_structural']:.2f}x")
    print(f"alpha=0.25:  ratio={rows_a[10]['P_behavioral']/rows_a[10]['P_structural']:.2f}x")
    print(f"alpha=0.5:   ratio={rows_a[20]['P_behavioral']/rows_a[20]['P_structural']:.2f}x")

    # ============================================================
    # Sensitivity heatmap (rho x p_recovery)
    # ============================================================
    rho_grid = np.linspace(0, 1, 21)
    prec_grid = np.linspace(0, 0.5, 21)
    ratio_grid = np.zeros((len(prec_grid), len(rho_grid)))
    for i, prec in enumerate(prec_grid):
        for j, rh in enumerate(rho_grid):
            s = structural_failure_rate(pis, PV, 3, rh, prec)
            b = behavioral_failure_rate(pis, ALPHA)
            ratio_grid[i, j] = b / s if s > 0 else 0
    print(f"\nSensitivity grid: {ratio_grid.min():.2f}x — {ratio_grid.max():.2f}x")

    # ============================================================
    # Build CSV
    # ============================================================
    dfs = []
    for label, rows in [("tier1_quorum_miss", rows_t1),
                         ("tier2_N_sweep", rows_n),
                         ("tier2_alpha_sweep", rows_a)]:
        df = pd.DataFrame(rows)
        df["tier"] = label
        dfs.append(df)
    bench = pd.concat(dfs, ignore_index=True)
    csv_path = save_data(bench, "detection_probability")
    print(f"\nData: {csv_path}  ({len(bench)} rows)")

    # ============================================================
    # Figure 1 — Tier 1: sim vs formula overlay
    # ============================================================
    apply_paper_style()
    fig1, ax1 = plt.subplots(figsize=(5.5, 4.0))
    markers = {3: "o", 5: "s"}
    colors = {3: "#0072B2", 5: "#D55E00"}
    for K in [3, 5]:
        mask = [r["K"] == K for r in rows_t1]
        rho_K = [r["rho"] for r, m in zip(rows_t1, mask) if m]
        sim_K = [r["simulated"] for r, m in zip(rows_t1, mask) if m]
        ana_K = [r["analytical"] for r, m in zip(rows_t1, mask) if m]
        ax1.plot(rho_K, ana_K, color=colors[K], linewidth=1.5, linestyle="-",
                 label=f"formula K={K}")
        ax1.scatter(rho_K, sim_K, marker=markers[K], color=colors[K],
                    s=20, alpha=0.7, zorder=5, label=f"sim K={K}")
    ax1.set(xlabel="ρ (intraclass correlation)",
            ylabel="P_miss (quorum miss probability)",
            title=f"Validation of quorum miss formula (p_v={PV})")
    ax1.legend(fontsize=8, frameon=False)
    ax1.grid(True, alpha=0.3)
    plt.tight_layout()
    svg1 = save_figure(fig1, "detection_quorum_validation")
    print(f"Figure: {svg1}")

    # ============================================================
    # Figure 2 — Tier 2: structural vs behavioral by N
    # ============================================================
    apply_paper_style()
    fig2, ax2 = plt.subplots(figsize=(5.5, 4.0))
    Ss = [r["P_structural"] for r in rows_n]
    Bs = [r["P_behavioral"] for r in rows_n]
    ax2.plot(N_vals, Bs, "s-", color="#D55E00", linewidth=1.5, markersize=4,
             label="Behavioral (cascading)")
    ax2.plot(N_vals, Ss, "o-", color="#0072B2", linewidth=1.5, markersize=4,
             label="Structural (quorum + recovery)")
    ax2.set(xlabel="N (pipeline stages)", ylabel="Failure probability",
            title="Structural vs behavioral failure by pipeline length")
    ax2.legend(fontsize=8, frameon=False)
    ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    svg2 = save_figure(fig2, "detection_structural_vs_behavioral")
    print(f"Figure: {svg2}")

    # ============================================================
    # Figure 3 — sensitivity heatmap
    # ============================================================
    apply_paper_style()
    fig3, ax3 = plt.subplots(figsize=(5.5, 4.0))
    im = ax3.imshow(ratio_grid, origin="lower", aspect="auto",
                    extent=[0, 1, 0, 0.5], cmap="YlOrRd")
    plt.colorbar(im, ax=ax3, label="P_behavioral / P_structural")
    ax3.set(xlabel="ρ (intraclass correlation)",
            ylabel="p_recovery (recovery loop failure)",
            title="Ratio sensitivity to ρ and p_recovery")
    plt.tight_layout()
    svg3 = save_figure(fig3, "detection_sensitivity")
    print(f"Figure: {svg3}")

    return bench, csv_path, fig1, svg1, fig2, svg2, fig3, svg3


@app.cell
def __():
    import marimo as mo
    return mo,


if __name__ == "__main__":
    app.run()
