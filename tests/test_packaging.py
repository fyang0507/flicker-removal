"""Public package entry points must work without a private photograph."""

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

from flicker_removal import __version__


def test_version_matches_project_metadata():
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    assert __version__ == project["version"]
    try:
        installed_version = importlib.metadata.version("flicker-removal")
    except importlib.metadata.PackageNotFoundError:
        return
    assert __version__ == installed_version


def test_module_entrypoint_lists_public_commands():
    result = subprocess.run(
        [sys.executable, "-m", "flicker_removal", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for command in ("calibrate", "calibrate-scene", "batch"):
        assert command in result.stdout


def test_module_entrypoint_reports_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "flicker_removal", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().endswith(__version__)
