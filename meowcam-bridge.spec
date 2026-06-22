# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for MeowCam Bridge v0.2 — Windows system tray app.

Builds MeowCamBridge.exe (windowed, no console) that:
  - Shows a tkinter loading screen on startup
  - Starts the uvicorn/FastAPI server in-process
  - Shows a system tray icon with Open/Close menu
  - Bundles Python so no separate install is needed
  - Supports NDI receive, OpenCV USB capture, and ATEM switcher control

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
import sys
import importlib.util
import glob

# PyInstaller exec's the spec file, so __file__ is not defined.
# SPECPATH is the directory containing the spec file.
spec_dir = pathlib.Path(os.environ.get("SPECPATH", os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()))

# ---------------------------------------------------------------------------
# Web UI static files
# ---------------------------------------------------------------------------
web_dir = spec_dir / "src" / "meowcam_bridge" / "web"
added_files = []
if web_dir.exists():
    added_files.append((str(web_dir), "meowcam_bridge/web"))

# ---------------------------------------------------------------------------
# Auto-detect NDI runtime DLL from ndi-python wheel
# ---------------------------------------------------------------------------
ndi_dlls = []
ndi_module_available = False
try:
    ndi_spec = importlib.util.find_spec("NDIlib")
    if ndi_spec and ndi_spec.origin:
        ndi_pkg_dir = pathlib.Path(ndi_spec.origin).parent
        # The wheel bundles Processing.NDI.Lib.*.dll next to the .pyd
        for dll in ndi_pkg_dir.glob("Processing.NDI.Lib*.dll"):
            ndi_dlls.append((str(dll), "."))
            print(f"[spec] Found NDI DLL: {dll.name}")
        # Check for the .pyd file to confirm module is present
        if any(ndi_pkg_dir.glob("_NDIlib*.pyd")):
            ndi_module_available = True
            print("[spec] NDIlib module files found")
        else:
            print("[spec] NDIlib .pyd not found — excluding from binary scan")
except Exception as exc:
    print(f"[spec] Warning: could not auto-detect NDI DLL: {exc}")

# ---------------------------------------------------------------------------
# Auto-detect OpenCV data files (haarcascades, DLLs)
# ---------------------------------------------------------------------------
cv2_data = []
try:
    import cv2
    cv2_dir = pathlib.Path(cv2.__file__).parent
    # Haar cascades and other data files
    cv2_data_dir = cv2_dir / "data"
    if cv2_data_dir.exists():
        cv2_data.append((str(cv2_data_dir), "cv2/data"))
        print(f"[spec] Found cv2 data: {cv2_data_dir}")
    # OpenCV DLLs live in cv2_dir itself (e.g. opencv_videoio_ffmpeg*.dll)
    for dll in cv2_dir.glob("*.dll"):
        cv2_data.append((str(dll), "."))
except Exception as exc:
    print(f"[spec] Warning: could not auto-detect cv2 data: {exc}")

# ---------------------------------------------------------------------------
# Runtime hooks directory
# ---------------------------------------------------------------------------
hook_dir = spec_dir / "hooks"
hook_dir.mkdir(exist_ok=True)

# Build the exclude list — NDI may need to be excluded in headless sessions
# where its DLL crashes during PyInstaller's binary dependency scan.
_excludes = []
if not ndi_module_available and ndi_dlls:
    _excludes.append("NDIlib")
    print("[spec] Excluding NDIlib from module collection (DLLs still bundled)")

a = Analysis(
    [str(spec_dir / "src" / "meowcam_bridge" / "tray_app.py")],
    pathex=[str(spec_dir / "src")],
    binaries=ndi_dlls,
    datas=added_files + cv2_data,
    hiddenimports=[
        # --- uvicorn / fastapi / pydantic ---
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        # --- tray / GUI ---
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "tkinter",
        # --- core bridge modules ---
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
        # --- v0.2 video + ATEM ---
        "meowcam_bridge.video",
        "meowcam_bridge.video_manager",
        "meowcam_bridge.atem",
        "numpy",
        "cv2",
        *(["NDIlib"] if ndi_module_available else []),
        "PyATEMMax",
        "PyATEMMax.ATEMProtocolEnums",
    ],
    hookspath=[str(hook_dir)],
    hooksconfig={},
    runtime_hooks=[str(hook_dir / "runtime_hook_ndi.py")] if (hook_dir / "runtime_hook_ndi.py").exists() else [],
    excludes=[e for e in _excludes if e],
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