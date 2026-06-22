# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for MeowCam Bridge — Windows system tray app.

Builds MeowCamBridge.exe (windowed, no console) that:
  - Shows a tkinter loading screen on startup
  - Starts the uvicorn/FastAPI server in-process
  - Shows a system tray icon with Open/Close menu
  - Bundles Python so no separate install is needed

To build:
    python build_windows.py

Or directly:
    pyinstaller meowcam-bridge.spec --clean --noconfirm

Output:
    dist/MeowCamBridge/MeowCamBridge.exe  (onedir mode, faster startup)
"""

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
import pathlib
import os

# PyInstaller exec's the spec file, so __file__ is not defined.
# SPECPATH is the directory containing the spec file.
spec_dir = pathlib.Path(os.environ.get("SPECPATH", os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()))

# Path to the web UI static files so they get embedded
web_dir = spec_dir / "src" / "meowcam_bridge" / "web"

added_files = []
if web_dir.exists():
    added_files.append((str(web_dir), "meowcam_bridge/web"))

a = Analysis(
    [str(spec_dir / "src" / "meowcam_bridge" / "tray_app.py")],
    pathex=[str(spec_dir / "src")],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "fastapi",
        "pydantic",
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "tkinter",
        "meowcam_bridge.app",
        "meowcam_bridge.bridge",
        "meowcam_bridge.config",
        "meowcam_bridge.protocols",
        "meowcam_bridge.protocols.base",
        "meowcam_bridge.protocols.visca",
        "meowcam_bridge.protocols.visca_commands",
        "meowcam_bridge.protocols.input_ptzoptics",
        "meowcam_bridge.protocols.input_ptzoptics_visca_udp",
        "meowcam_bridge.protocols.output_sony_brbk",
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
    [],
    exclude_binaries=True,
    name="MeowCamBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window — tray app only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",  # Uncomment when an icon is available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="MeowCamBridge",
)