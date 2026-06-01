"""Figure and data I/O helpers.

Each study calls save_figure / save_data to emit deterministic
assets into its own outputs/ directory.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def _caller_output_dir() -> Path:
    """Deduce the calling study's outputs/ directory.

    First checks the MARIMO_STUDY_OUTPUT environment variable (set by the
    runner).  Then walks the call stack to find the notebook's parent dir.
    Falls back to CWD/outputs/.
    """
    import os as _os
    import inspect

    # Runner can set this env var to point directly at the outputs dir
    env_output = _os.environ.get("MARIMO_STUDY_OUTPUT")
    if env_output:
        outputs = Path(env_output).resolve()
        outputs.mkdir(exist_ok=True)
        return outputs

    # Walk the call stack to find the notebook file's directory
    frame = inspect.currentframe()
    try:
        while frame:
            fname = frame.f_code.co_filename
            # Skip frames inside _common itself
            if fname and _os.sep + "_common" + _os.sep not in fname:
                study_dir = Path(fname).resolve().parent
                outputs = study_dir / "outputs"
                outputs.mkdir(exist_ok=True)
                return outputs
            frame = frame.f_back
    finally:
        del frame

    # Fallback: assume cwd is the study directory
    outputs = Path.cwd() / "outputs"
    outputs.mkdir(exist_ok=True)
    return outputs


def save_figure(fig: plt.Figure, name: str, ext: str = "svg") -> Path:
    """Save a matplotlib figure to the study's outputs/ directory.

    Args:
        fig: matplotlib Figure.
        name: Base filename (without extension).
        ext:  Output format — svg by default (vector, paper-ready).

    Returns:
        Filesystem path to the saved file.
    """
    outputs = _caller_output_dir()
    stem = Path(name).stem
    fname = f"{stem}.{ext}"
    path = outputs / fname
    fig.savefig(str(path), format=ext)
    plt.close(fig)
    return path


def save_data(df: pd.DataFrame, name: str) -> Path:
    """Save a DataFrame as CSV to the study's outputs/ directory.

    Args:
        df:   pandas DataFrame.
        name: Base filename (without extension).

    Returns:
        Filesystem path to the saved file.
    """
    outputs = _caller_output_dir()
    stem = Path(name).stem
    fname = f"{stem}.csv"
    path = outputs / fname
    df.to_csv(path, index=False)
    return path
