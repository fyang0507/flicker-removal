"""Verify built archives contain the CLI and license, never private RAW data."""

import email
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def check_names(names):
    forbidden = {"photos", "outputs", "local", ".venv", ".git", "__pycache__"}
    private_suffixes = {".arw", ".dng", ".xmp", ".npz", ".npy"}
    for name in names:
        path = PurePosixPath(name)
        assert not forbidden.intersection(path.parts), name
        assert path.suffix.lower() not in private_suffixes, name
    assert any(name.endswith("flicker_removal/__main__.py") for name in names)
    assert any(PurePosixPath(name).name == "LICENSE" for name in names)
    assert any(PurePosixPath(name).name == "NOTICE" for name in names)


def main(directory):
    directory = Path(directory)
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Expected one wheel and one source distribution"
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        check_names(names)
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(wheel.read(metadata_path))
        assert metadata["Name"] == "flicker-removal"
        assert metadata["License-Expression"] == "MIT"
        entrypoints = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        assert b"flicker-removal = flicker_removal.cli:main" in wheel.read(entrypoints)
    with tarfile.open(sources[0]) as source:
        check_names(source.getnames())
    print(f"Verified wheel and source distribution: {metadata['Name']} {metadata['Version']}")


if __name__ == "__main__":
    main(sys.argv[1])
