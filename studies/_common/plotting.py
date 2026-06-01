"""Shared plot styling for paper-ready figures.

Call apply_paper_style() at the top of every study to get
consistent fonts, sizing, colour palette, and SVG output defaults.
"""

import matplotlib as mpl


FONT_FAMILY = "serif"
FONT_SIZE = 10
FIG_WIDTH_IN = 5.5    # single column
FIG_HEIGHT_IN = 3.5
DPI = 150

# Colour-blind-friendly palette (Wong, 2011)
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#F0E442", "#56B4E9", "#E69F00", "#CC79A7"]


def apply_paper_style() -> None:
    """Configure matplotlib for paper-ready output.

    Sets serif fonts, consistent figure size, and the paper palette.
    """
    mpl.rcParams.update({
        "font.family": FONT_FAMILY,
        "font.size": FONT_SIZE,
        "axes.titlesize": FONT_SIZE + 1,
        "axes.labelsize": FONT_SIZE,
        "xtick.labelsize": FONT_SIZE - 1,
        "ytick.labelsize": FONT_SIZE - 1,
        "legend.fontsize": FONT_SIZE - 1,
        "figure.dpi": DPI,
        "savefig.dpi": DPI,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
    })
    mpl.rcParams["axes.prop_cycle"] = mpl.cycler(color=PALETTE)
