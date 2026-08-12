import importlib
import importlib.metadata

import baloo


def test_version_matches_package_metadata() -> None:
    assert baloo.__version__ == importlib.metadata.version("baloo")


def test_version_falls_back_when_package_is_not_installed() -> None:
    def raise_package_not_found(_distribution_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    original_version = importlib.metadata.version
    importlib.metadata.version = raise_package_not_found
    try:
        assert importlib.reload(baloo).__version__ == "unknown"
    finally:
        importlib.metadata.version = original_version
        importlib.reload(baloo)
