from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("snodo")
except PackageNotFoundError:
    __version__ = "unknown"
