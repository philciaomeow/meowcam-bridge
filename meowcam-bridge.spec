# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec stub for MeowCam Bridge.

To build:
    pyinstaller meowcam-bridge.spec --clean

Output:
    dist/meowcam-bridge/  (standalone folder, or single .exe if console=False)

Before building:
1. Bridge core must be complete and tested.
2. Decide: console=True (logs visible) vs console=False (windowed, logs to file).
3. Add an icon file and update icon= below.
4. Test on a real Windows machine for firewall and port binding behaviour.
"""

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
import pathlib

# Path to the web UI static files so they get embedded
web_dir = pathlib.Path(__file__).parent / "src" / "meowcam_bridge" / "web"

added_files = []
if web_dir.exists():
    added_files.append((str(web_dir), "meowcam_bridge/web"))

a = Analysis(
    ["src/meowcam_bridge/app.py"],
    pathex=[],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "fastapi",
        "pydantic",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="meowcam-bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Set False for windowed mode (no console). See PACKAGING.md.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",  # Uncomment when an icon is available
)
