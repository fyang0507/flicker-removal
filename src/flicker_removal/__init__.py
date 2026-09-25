"""Session-calibrated, non-generative flicker removal."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("flicker-removal")
except PackageNotFoundError:
    # Permit source-only use before installation (for example via PYTHONPATH).
    __version__ = "0.1.0"

__all__ = ["__version__"]
