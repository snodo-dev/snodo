from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)

from importlib.metadata import version, PackageNotFoundError
try:
    __version__ = version("snodo")
except PackageNotFoundError:
    __version__ = "unknown"
