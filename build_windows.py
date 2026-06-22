#!/usr/bin/env python3
"""MeowCam Bridge — Windows build script (v0.2).

Produces a distributable .zip containing:
  - MeowCamBridge.exe (system tray app, no console window)
  - All bundled Python + dependencies (via PyInstaller --onedir)
  - Web UI assets (embedded in exe)
  - NDI runtime DLLs (auto-detected from ndi-python wheel)
  - OpenCV data files and DLLs
  - README.md, SETUP.md, PACKAGING.md
  - meowcam-bridge.json (default config, auto-created on first run)

Usage:
    python build_windows.py

Prerequisites:
    pip install pyinstaller pystray Pillow ndi-python opencv-python-headless PyATEMMax

After code changes, just run this script again. No manual recreation needed.
"""

from __future__ import annotations

import importlib.util
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
HOOK_DIR = ROOT / "hooks"


def read_version() -> str:
    """Read version from pyproject.toml."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def find_ndi_dlls() -> list[pathlib.Path]:
    """Auto-detect NDI runtime DLLs bundled with the ndi-python wheel."""
    dlls: list[pathlib.Path] = []
    try:
        ndi_spec = importlib.util.find_spec("NDIlib")
        if ndi_spec and ndi_spec.origin:
            ndi_pkg_dir = pathlib.Path(ndi_spec.origin).parent
            for dll in ndi_pkg_dir.glob("Processing.NDI.Lib*.dll"):
                dlls.append(dll)
                print(f"  Found NDI DLL: {dll}")
    except Exception as exc:
        print(f"  Warning: could not locate NDI DLL: {exc}")
    return dlls


def ensure_runtime_hook() -> pathlib.Path:
    """Ensure the NDI runtime hook exists."""
    HOOK_DIR.mkdir(exist_ok=True)
    hook_file = HOOK_DIR / "runtime_hook_ndi.py"
    if not hook_file.exists():
        hook_file.write_text(
            '''\
import os, sys
if hasattr(sys, "_MEIPASS") and hasattr(os, "add_dll_directory"):
    try:
        os.add_dll_directory(sys._MEIPASS)
    except Exception:
        pass
''',
            encoding="utf-8",
        )
        print(f"  Created runtime hook: {hook_file}")
    return hook_file


def clean_old_builds() -> None:
    """Remove previous PyInstaller output."""
    for d in [DIST, BUILD]:
        if d.exists():
            print(f"Cleaning {d}…")
            try:
                shutil.rmtree(d)
            except PermissionError:
                # Windows may hold file handles (indexing, AV, etc.)
                # Try renaming then deleting the renamed copy
                import time
                suffix = f"_old_{int(time.time())}"
                try:
                    d.rename(d.with_name(d.name + suffix))
                    print(f"  Renamed to {d.name}{suffix} (will clean next time)")
                except Exception:
                    print(f"  WARNING: could not remove {d}, building alongside it")
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
    for doc in ["README.md", "SETUP.md", "PACKAGING.md"]:
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


def check_build_deps() -> list[str]:
    """Return list of missing build/runtime dependencies."""
    missing = []
    for pkg, mod in [
        ("pyinstaller", "PyInstaller"),
        ("pystray", "pystray"),
        ("Pillow", "PIL"),
        ("ndi-python", "NDIlib"),
        ("opencv-python-headless", "cv2"),
        ("PyATEMMax", "PyATEMMax"),
    ]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    return missing


def main() -> int:
    version = read_version()
    print(f"\n{'='*50}")
    print(f"  MeowCam Bridge v{version} — Windows Build")
    print(f"{'='*50}\n")

    # Check prerequisites
    missing = check_build_deps()
    if missing:
        print(f"Missing build dependencies: {', '.join(missing)}")
        print(f"Install with: pip install {' '.join(missing)}")
        return 1

    # Pre-flight checks
    ndi_dlls = find_ndi_dlls()
    if not ndi_dlls:
        print("  Warning: no NDI DLLs found — NDI support will not work in the frozen app")
    ensure_runtime_hook()

    # NDI headless-session check
    # NDIlib's DLL initialization crashes in non-interactive SSH/RDP sessions.
    # If we're in such a session, warn the user that the build may fail.
    ndi_import_ok = False
    try:
        import NDIlib as _ndi_test
        ndi_import_ok = True
    except Exception as ndi_exc:
        print(f"  Warning: NDIlib import failed ({ndi_exc}) — this is expected in headless sessions")
        print("  The frozen app will still bundle NDI DLLs, but NDI features require an interactive desktop session.")

    clean_old_builds()

    if not run_pyinstaller():
        print("PyInstaller build failed!")
        if not ndi_import_ok:
            print("\n  NDIlib import failed during build. If this is a headless SSH/RDP session,")
            print("  NDI's runtime DLL cannot initialise. Two options:")
            print("    1. Run the build from an interactive Windows desktop session.")
            print("    2. Temporarily remove 'NDIlib' from hiddenimports in meowcam-bridge.spec,")
            print("       build, then manually copy the NDI DLL into dist/MeowCamBridge/.")
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