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
- **Build process is repeatable**: one command rebuilds after code changes, no manual recreation.

## Current state (v0.1.0)

- Python 3.11+ source package with `pyproject.toml`.
- FastAPI + uvicorn local web UI on `http://localhost:8080`.
- Config auto-created as JSON in working directory (`meowcam-bridge.json`).
- All core features implemented and tested (104 tests passing):
  - UDP bridge core with shared camera socket for multi-camera.
  - PTZOptics PT-JOY-G4 input profiles (Sony VISCA UDP and generic VISCA UDP).
  - Sony BRC-H900/BRBK-IP10 output profile with OSD translation.
  - Preset drive speed injection before preset recalls.
  - Web UI: presets (4-camera quadrants), manual control, diagnostics, settings.
  - Per-camera movement speed (slow/medium/fast) persisted to config.
  - `launch.bat` for Windows double-click startup.
  - `launch.sh` for Linux/macOS.

## v0.2 packaging plan — System tray Windows app

### Approach: PyInstaller + `pystray` + `tkinter` loading screen

| Component | Choice | Why |
|-----------|--------|-----|
| Python bundling | PyInstaller `--onedir` | Bundles Python interpreter + all deps into a folder. No separate Python install needed. |
| System tray | `pystray` + `Pillow` | Lightweight, pure-Python, works on Windows. Shows tray icon with menu. |
| Loading screen | `tkinter` (built-in) | No extra deps. Simple "Starting MeowCam Bridge…" window that auto-closes when the server is ready. |
| Server process | `subprocess` of bundled `meowcam-bridge.exe` | The tray app spawns the server as a child process. Killing the tray kills the server. |
| Browser open | `webbrowser.open()` | Opens default browser to `http://localhost:8080` when "Open Control Surface" is clicked. |

### Why not a single-file `.exe`?

`--onefile` mode extracts to a temp dir on every launch (slow startup, 5-10 seconds). `--onedir` mode is a folder with an `.exe` inside — faster startup, easier to debug, and the folder can be zipped for distribution. We'll zip the `dist/MeowCamBridge/` folder.

### Build script: `build_windows.py`

A single Python script that:
1. Installs build dependencies (`pyinstaller`, `pystray`, `Pillow`).
2. Runs PyInstaller with the tray app spec.
3. Copies `web/` assets, `launch.bat`, and docs into the output folder.
4. Zips the result.

**This script is the entire build process.** After code changes, just run:

```bash
python build_windows.py
```

And the new `.zip` is in `dist/`. No manual steps, no recreation.

### Tray app: `src/meowcam_bridge/tray_app.py`

```python
"""MeowCam Bridge system tray application for Windows.

Shows a tray icon with menu options:
  - Open Control Surface (opens browser to http://localhost:8080)
  - Close Server (stops the bridge and exits)

On startup, shows a small tkinter loading window that auto-closes
when the server responds to a health check.
"""
```

### Updated `meowcam-bridge.spec`

The spec will build TWO executables:
1. `meowcam-bridge.exe` — the server (console=False, hidden).
2. `MeowCamBridge.exe` — the tray app (console=False, windowed).

Or simpler: build the tray app as the main entry point, which spawns the server as a subprocess using the same bundled Python. This avoids needing two .exe files.

**Chosen approach:** Single `.exe` (`MeowCamBridge.exe`) that starts the server in-process using uvicorn's programmatic API (not subprocess), shows the tray icon, and manages the lifecycle. This is cleaner — no subprocess management, no orphan processes.

### Firewall / UDP notes

The bridge listens on UDP ports for controller packets and sends to cameras. Windows Defender Firewall may block these.

**Inbound:** One UDP port per enabled camera route (default 52382–52389).
**Outbound:** UDP to camera IPs on port 52381 (Sony BRBK-IP10).

The tray app should show a firewall reminder on first run. For PyInstaller builds, the firewall rule targets `MeowCamBridge.exe`.

## File locations (packaging decisions)

| What | Development | Windows packaged | Linux/macOS installed |
|------|-------------|------------------|----------------------|
| Config | `./meowcam-bridge.json` (cwd) | `%LOCALAPPDATA%\MeowCamBridge\config.json` or alongside `.exe` | `~/.config/meowcam-bridge/config.json` or cwd |
| Logs | stdout/stderr only | `%LOCALAPPDATA%\MeowCamBridge\logs\bridge.log` | `~/.local/share/meowcam-bridge/logs/bridge.log` |
| Web UI | embedded in package (`src/meowcam_bridge/web/`) | embedded in `.exe` via PyInstaller `datas` | embedded in package |

**Decision for v0.2:** Keep config alongside the `.exe` for simplicity. The operator unzips a folder, double-clicks `MeowCamBridge.exe`, and everything is self-contained. Logs go to a `logs/` subfolder.

## Build workflow (after code changes)

```bash
# 1. Make code changes
# 2. Run tests
pytest

# 3. Build Windows package (produces dist/MeowCamBridge-<version>.zip)
python build_windows.py

# 4. Test the zip on a Windows VM
# 5. Upload to GitHub Releases and tag
git tag v0.2.0
git push origin v0.2.0
```

That's it. No manual recreation of the build process.

## Versioning

- Version lives in `pyproject.toml` and `src/meowcam_bridge/__init__.py`.
- Git tags: `v0.1.0`, `v0.2.0`, etc.
- Packaged releases: GitHub Releases with `.zip` of build output.
- The build script reads the version from `pyproject.toml` automatically.

## Testing the package

```bash
# Clean install test
pip install .
meowcam-bridge --help

# Run tests
pytest

# Run with example config
python -m meowcam_bridge --config examples/config.example.json
```

## Checklist for releasing v0.2 (system tray Windows app)

- [ ] `tray_app.py` implemented with pystray + tkinter loading screen.
- [ ] `build_windows.py` build script working.
- [ ] `meowcam-bridge.spec` updated for windowed mode + tray app.
- [ ] PyInstaller build produces working `MeowCamBridge.exe`.
- [ ] Tray icon shows "MeowCam Bridge Server" with Open/Close menu.
- [ ] Loading screen appears on startup and closes when server is ready.
- [ ] No command window visible.
- [ ] Browser opens to `http://localhost:8080` from tray menu.
- [ ] Firewall prompt handled (exe allowed through).
- [ ] Config and logs saved alongside the exe.
- [ ] Tested on Windows 10 and Windows 11.
- [ ] Zip file tested by unzipping to a fresh location and running.