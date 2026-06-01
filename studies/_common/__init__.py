"""Common helpers for studies — seeded RNG, figure/data I/O, shared styling.

Import from here in every study notebook:
    from _common import rng, save_figure, save_data, apply_paper_style
"""

from _common.seeds import SEED, rng
from _common.io import save_figure, save_data
from _common.plotting import apply_paper_style

__all__ = ["SEED", "rng", "save_figure", "save_data", "apply_paper_style"]
