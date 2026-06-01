"""Smoke study — end-to-end scaffold validation (Task 8.1).

Demonstrates the full study lifecycle:
  1. Seeded RNG from _common.seeds.rng()
  2. Paper-styled plot via _common.plotting.apply_paper_style()
  3. Save figure (SVG) and data (CSV) via _common.io
  4. Deterministic output — identical on every run

Run headless:  marimo run studies/_smoke/notebook.py
Run interactively: marimo edit studies/_smoke/notebook.py

Once a real study lands, this file can be deleted.
"""

import sys
from pathlib import Path

# Ensure _common is importable when run from repo root or from studies/
_common_parent = Path(__file__).resolve().parent.parent
if str(_common_parent) not in sys.path:
    sys.path.insert(0, str(_common_parent))

import marimo  # noqa: E402 — sys.path must be set first

__generated_with = "0.9.0"
app = marimo.App(width="medium")


@app.cell
def __():
    import numpy as np
    from _common import SEED, rng, apply_paper_style, save_figure, save_data

    apply_paper_style()
    gen = rng(SEED)
    return SEED, apply_paper_style, gen, np, rng, save_figure, save_data


@app.cell
def __(gen, np):
    n = 128
    x = np.linspace(0, 4 * np.pi, n)
    y_signal = np.sin(x)
    y_noisy = y_signal + 0.15 * gen.standard_normal(n)
    return n, x, y_noisy, y_signal


@app.cell
def __(SEED, apply_paper_style, x, y_noisy, y_signal):
    import matplotlib.pyplot as plt

    apply_paper_style()
    fig, ax = plt.subplots(figsize=(5.5, 3.5))
    ax.plot(x, y_signal, label="signal", linewidth=1.5)
    ax.scatter(x, y_noisy, s=8, alpha=0.6, label="samples")
    ax.set(xlabel="x", ylabel="y", title=f"Smoke test (seed={SEED})")
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    return ax, fig


@app.cell
def __(fig, x, y_noisy, save_figure, save_data):
    import pandas as pd

    svg_path = save_figure(fig, "smoke_test")
    df = pd.DataFrame({"x": x, "y_noisy": y_noisy})
    csv_path = save_data(df, "smoke_data")
    print(f"Figure: {svg_path}")
    print(f"Data:   {csv_path}")
    return csv_path, df, svg_path


@app.cell
def __():
    import hashlib
    from pathlib import Path as Plib

    def sha256(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    outputs = Plib(__file__).resolve().parent / "outputs"
    svg_hash = sha256(outputs / "smoke_test.svg")
    csv_hash = sha256(outputs / "smoke_data.csv")
    print(f"SVG hash: {svg_hash}")
    print(f"CSV hash: {csv_hash}")

    # Expected hashes for seed=42 — update these after the first run.
    # They serve as a regression guard: if they change, the study outputs
    # are no longer deterministic.
    EXPECTED_SVG = "8b0906c97826abcbbc738f47dbcc5d22a7749ef6987a564e2de24ad94fe34e61"
    EXPECTED_CSV = "4d481dc76cbc7e58107ee509c4dfaee891fcfb49d2392e5a509c46d07ae5f422"

    if svg_hash == EXPECTED_SVG and csv_hash == EXPECTED_CSV:
        print("\nDeterminism confirmed — outputs match expected hashes.")
    else:
        print("\nDeterminism check FAILED — outputs have changed.")
        print(f"  SVG expected: {EXPECTED_SVG}")
        print(f"  SVG got:      {svg_hash}")
        print(f"  CSV expected: {EXPECTED_CSV}")
        print(f"  CSV got:      {csv_hash}")
    return sha256, svg_hash, csv_hash


@app.cell
def __():
    import marimo as mo
    return mo,


if __name__ == "__main__":
    app.run()
