"""Baloo - AI-powered GitHub code review agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("baloo")
except PackageNotFoundError:
    # Package metadata is unavailable when importing directly from a source tree.
    __version__ = "unknown"
