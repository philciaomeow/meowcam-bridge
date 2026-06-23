# MeowCam Bridge — Packaging & Deployment Guide

> Target audience: developers / packagers. For onsite operator setup, see `SETUP.md`.

## Goals

- Run on Windows without requiring Python installation (PyInstaller `.exe` with bundled Python).
- Run on Linux/macOS with Python 3.11+ installed.
- Zero external dependencies at runtime (no internet, no cloud, no Docker, no k3s).
- Config, logs, and UI all local.
- Non-technical operator can start the app with a double-click.
- System tray icon with "Open Control Surface" and "Close Server" options.
- GUI loading screen on startup (no scary command window).
- **Build process is repeatable**: one command rebuilds after code changes.

## Current state (v0.2.0)

- Python 3.11+ source package with `pyproject.toml`.
- FastAPI + uvicorn local web UI on `http://localhost:8080`.
- Config auto-created as JSON in working directory (`meowcam-bridge.json`).
- 169 tests passing.
- **All v0.2 features implemented:**
  - NDI video capture with discovery endpoint
  - USB/HDMI capture card support with device dropdown
  - Shared USB device pool (multiple routes, one device)
  - Crop/region presets (Full Frame, TL, TR, BL, BR, Custom)
  - ATEM switcher integration (SuperSource, tally)
  - System tray app (pystray + tkinter loading screen)
  - Live preview grid with PGM/PVW tally indicators

## Runtime dependencies (v0.2)

| Package | Purpose | Bundled by PyInstaller? | Optional? |
|---------|---------|------------------------|-----------|
| `fastapi` + `uvicorn` | Web server | Yes | No (core) |
| `pydantic` | Config models | Yes | No (core) |
| `PyATEMMax` | ATEM switcher control | Yes — pure Python | Yes — bridge works without ATEM |
| `numpy` | Frame handling | Yes | Required by video features |
| `opencv-python-headless` | USB/HDMI capture, JPEG encoding, test pattern | Yes — large binary wheels | Yes — test pattern works without it |
| `ndi-python` | NDI video receive | Yes — C extension + runtime DLL | Yes — bridge falls back to test pattern |
| `pystray` + `Pillow` | System tray app (Windows) | Yes | Yes — only needed for tray mode |

All video/ATEM deps are **optional at runtime** — the bridge starts and functions fully as a PTZ controller without any of them. They are only needed when the corresponding features are enabled in the config.

## PyInstaller bundling specifics

### NDI runtime DLL

The `ndi-python` wheel bundles `Processing.NDI.Lib.x64.dll`. PyInstaller does **not** automatically detect this non-Python DLL:

1. **Auto-detection** in `meowcam-bridge.spec`: searches the `NDIlib` package directory for `Processing.NDI.Lib*.dll` and adds them to `binaries=`.
2. **Runtime hook** (`hooks/runtime_hook_ndi.py`): calls `os.add_dll_directory(sys._MEIPASS)` so the bundled DLL is found at runtime in onedir mode.
3. **Fallback** in `video_manager.py`: if `NDIlib` import fails, the route falls back to `TestPatternSource`.

### OpenCV data files

OpenCV ships with haarcascade XML files and FFmpeg DLLs. The spec auto-detects:
- `cv2/data/` directory → bundled as `cv2/data`
- Any `.dll` files in the `cv2` package root → bundled at top level

### PyATEMMax

Pure Python — no special binary handling needed. Added to `hiddenimports` in the spec.

## Approach: PyInstaller + `pystray` + `tkinter` loading screen

| Component | Choice | Why |
|-----------|--------|-----|
| Python bundling | PyInstaller `--onedir` | Bundles Python interpreter + all deps into a folder. |
| System tray | `pystray` + `Pillow` | Lightweight, pure-Python, works on Windows. |
| Loading screen | `tkinter` (built-in) | No extra deps. "Starting MeowCam Bridge…" window, auto-closes when server ready. |
| Server process | In-process uvicorn | Tray app runs uvicorn directly (no subprocess management). |
| Browser open | `webbrowser.open()` | Opens default browser when "Open Control Surface" is clicked. |

### Why not a single-file `.exe`?

`--onefile` mode extracts to a temp dir on every launch (slow startup, 5-10 seconds). `--onedir` mode is a folder with an `.exe` inside — faster startup, easier to debug. The folder can be zipped for distribution.

## Build script: `build_windows.py`

One command produces the distributable:

```bash
python build_windows.py
```

This script:
1. Installs build dependencies (`pyinstaller`, `pystray`, `Pillow`).
2. Runs PyInstaller with the tray app spec.
3. Copies `web/` assets, `launch.bat`, and docs into the output folder.
4. Zips the result into `dist/MeowCamBridge-<version>.zip`.

**After code changes, just run `python build_windows.py` again.** No manual steps.

## Cross-platform notes

### Windows
- NDI works natively (built-in mDNS, no avahi needed)
- USB capture via DirectShow/MediaFoundation backend (cv2.VideoCapture)
- Shared USB device pool needs testing — Windows may lock capture devices exclusively
- System tray app works with pystray

### Linux
- NDI requires avahi-daemon + multicast route (`ip route add 224.0.0.0/4 dev <iface>`)
- USB capture via V4L2 backend (cv2.VideoCapture)
- Shared USB device pool verified working
- Use `launch.sh` instead of tray app

## Firewall / UDP notes

The bridge listens on UDP ports for controller packets and sends to cameras. Windows Defender Firewall may block these.

**Inbound:** One UDP port per enabled camera route (default 52382–52389).
**Outbound:** UDP to camera IPs on port 52381 (Sony BRBK-IP10).

The tray app should show a firewall reminder on first run. For PyInstaller builds, the firewall rule targets `MeowCamBridge.exe`.

## Build workflow (after code changes)

```bash
# 1. Make code changes
# 2. Run tests
pytest

# 3. Build Windows package
python build_windows.py

# 4. Test the zip on a Windows VM
# 5. Upload to GitHub Releases and tag
git tag v0.2.0
git push origin v0.2.0
```

## Versioning

- Version lives in `pyproject.toml` and `src/meowcam_bridge/__init__.py`.
- Git tags: `v0.1.0`, `v0.1.1`, `v0.2.0`, etc.
- Packaged releases: GitHub Releases with `.zip` of build output.
- The build script reads the version from `pyproject.toml` automatically.

## Release checklist (v0.2)

- [x] `tray_app.py` implemented with pystray + tkinter loading screen
- [x] `build_windows.py` build script working
- [x] `meowcam-bridge.spec` updated for windowed mode + tray app + NDI DLLs + OpenCV
- [x] Tray icon shows "MeowCam Bridge" with Open/Close menu
- [x] Loading screen appears on startup
- [x] No command window visible
- [ ] PyInstaller build tested on Windows 10 with USB capture
- [ ] NDI tested on Windows (native mDNS — no workaround needed)
- [ ] Shared USB device pool tested on Windows
- [ ] Config and logs saved alongside the exe
- [ ] Zip file tested on fresh Windows install
