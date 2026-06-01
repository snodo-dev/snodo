"""Deterministic RNG for all studies.

Every study draws its RNG from here.  Never use bare np.random.
"""

import numpy as np

# Master seed for the entire Wave 8 study suite.
# Changing this regenerates ALL studies.
SEED = 42


def rng(seed: int = SEED) -> np.random.Generator:
    """Return a seeded NumPy Generator for reproducible randomness.

    Args:
        seed: Override the default SEED.  Call without args in studies.

    Returns:
        A numpy.random.Generator instance (modern API, not legacy RandomState).
    """
    return np.random.default_rng(seed)
