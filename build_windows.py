#!/usr/bin/env python3
"""MeowCam Bridge — Windows build script.

Produces a distributable .zip containing:
  - MeowCamBridge.exe (system tray app, no console window)
  - All bundled Python + dependencies (via PyInstaller --onedir)
  - Web UI assets (embedded in exe)
  - README.md, SETUP.md
  - meowcam-bridge.json (default config, auto-created on first run)

Usage:
    python build_windows.py

Prerequisites:
    pip install pyinstaller pystray Pillow

After code changes, just run this script again. No manual recreation needed.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
WEB_DIR = ROOT / "src" / "meowcam_bridge" / "web"
SPEC_FILE = ROOT / "meowcam-bridge.spec"


def read_version() -> str:
    """Read version from pyproject.toml."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def clean_old_builds() -> None:
    """Remove previous PyInstaller output."""
    for d in [DIST, BUILD]:
        if d.exists():
            print(f"Cleaning {d}…")
            shutil.rmtree(d)
    DIST.mkdir(exist_ok=True)


def run_pyinstaller() -> bool:
    """Run PyInstaller with the tray app spec."""
    print("Running PyInstaller…")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(SPEC_FILE),
        "--clean",
        "--noconfirm",
    ]
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode == 0


def copy_docs(output_dir: pathlib.Path) -> None:
    """Copy documentation into the output folder."""
    for doc in ["README.md", "SETUP.md"]:
        src = ROOT / doc
        if src.exists():
            shutil.copy2(src, output_dir / doc)
            print(f"  Copied {doc}")


def create_zip(output_dir: pathlib.Path, version: str) -> pathlib.Path:
    """Zip the output folder for distribution."""
    zip_path = DIST / f"MeowCamBridge-v{version}.zip"
    print(f"Creating {zip_path.name}…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in output_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(output_dir.parent)
                zf.write(file_path, arcname)
    print(f"  {zip_path.stat().st_size / (1024*1024):.1f} MB")
    return zip_path


def main() -> int:
    version = read_version()
    print(f"\n{'='*50}")
    print(f"  MeowCam Bridge v{version} — Windows Build")
    print(f"{'='*50}\n")

    # Check prerequisites
    missing = []
    try:
        import pystray  # noqa: F401
    except ImportError:
        missing.append("pystray")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        missing.append("Pillow")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        missing.append("pyinstaller")

    if missing:
        print(f"Missing build dependencies: {', '.join(missing)}")
        print("Install with: pip install {' '.join(missing)}")
        return 1

    clean_old_builds()

    if not run_pyinstaller():
        print("PyInstaller build failed!")
        return 1

    # PyInstaller output folder
    output_dir = DIST / "MeowCamBridge"
    if not output_dir.exists():
        # PyInstaller might name it differently — find it
        candidates = list(DIST.iterdir())
        dirs = [d for d in candidates if d.is_dir()]
        if len(dirs) == 1:
            output_dir = dirs[0]
        else:
            print(f"Cannot find build output in {DIST}. Contents: {[d.name for d in candidates]}")
            return 1

    print(f"\nBuild output: {output_dir}")

    # Copy docs
    print("\nCopying documentation…")
    copy_docs(output_dir)

    # Create distributable zip
    zip_path = create_zip(output_dir, version)

    print(f"\n{'='*50}")
    print(f"  Build complete!")
    print(f"  Output: {zip_path}")
    print(f"  Folder: {output_dir}")
    print(f"  Size: {zip_path.stat().st_size / (1024*1024):.1f} MB")
    print(f"{'='*50}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())