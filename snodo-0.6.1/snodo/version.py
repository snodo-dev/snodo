"""Package version, resolved from installed distribution metadata.

``snodo`` is a PEP 420 namespace package (no top-level ``__init__.py``), so the
version lives in this module rather than on the package object. Import it as
``from snodo.version import __version__``.
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("snodo")
except PackageNotFoundError:
    __version__ = "unknown"
