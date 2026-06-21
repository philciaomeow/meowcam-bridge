# MeowCam Bridge — Packaging & Deployment Plan

> Target audience: developers / packagers. For onsite operator setup, see `SETUP.md`.

## Goals

- Run on Windows without requiring Python installation (future: PyInstaller `.exe`).
- Run on Linux/macOS with Python 3.11+ installed.
- Zero external dependencies at runtime (no internet, no cloud, no Docker, no k3s).
- Config, logs, and UI all local.
- Non-technical operator can start the app with a double-click.

## Current state (v0.1)

- Python 3.11+ source package with `pyproject.toml`.
- FastAPI + uvicorn local web UI on `http://localhost:8080`.
- Config auto-created as JSON in working directory (`meowcam-bridge.json`).
- UDP bridge core, PTZOptics input profile, Sony BRC-H900/BRBK-IP10 output profile, diagnostics, settings, presets, and manual controls are implemented and covered by tests.

## File locations (packaging decisions)

| What | Development | Windows packaged | Linux/macOS installed |
|------|-------------|------------------|----------------------|
| Config | `./meowcam-bridge.json` (cwd) | `%LOCALAPPDATA%\MeowCamBridge\config.json` or alongside `.exe` | `~/.config/meowcam-bridge/config.json` or cwd |
| Logs | stdout/stderr only | `%LOCALAPPDATA%\MeowCamBridge\logs\bridge.log` | `~/.local/share/meowcam-bridge/logs/bridge.log` |
| Web UI | embedded in package (`src/meowcam_bridge/web/`) | embedded in `.exe` | embedded in package |

**Decision for v0.1:** Keep config and logs in the working directory for simplicity. This lets an operator unzip a folder, double-click `launch.bat`, and have everything self-contained in that folder. Migrate to OS-specific paths once PyInstaller packaging lands.

## Launch scripts

### Windows: `launch.bat`

- Checks for Python in PATH.
- If missing, shows a friendly message pointing to python.org.
- Installs the package in editable mode (dev) or from wheel.
- Starts `meowcam-bridge.exe` (if PyInstaller) or `python -m meowcam_bridge`.
- Opens the default browser to `http://localhost:8080` after a short delay.
- Keeps a console window open so logs are visible.

### Linux/macOS: `launch.sh`

- Same behaviour as `launch.bat` but for POSIX shells.
- Detects Python 3.11+.
- Offers to create a venv if desired.

## Firewall / UDP notes

The bridge listens on UDP ports for controller packets and sends to cameras. Windows Defender Firewall may block these.

**Inbound:** One UDP port per enabled camera route (default 52380–52387).
**Outbound:** UDP to camera IPs on port 52381 (Sony BRBK-IP10).

`launch.bat` and `SETUP.md` include a note to allow Python through the firewall. For PyInstaller, the rule will need to target the `.exe` instead.

## PyInstaller roadmap (future)

1. Add `meowcam-bridge.spec` with:
   - `src/meowcam_bridge/web/` as embedded datas.
   - Console mode for log visibility (or windowed + log file).
   - Icon file (optional).
2. Build command:
   ```bash
   pyinstaller meowcam-bridge.spec --clean
   ```
3. Output: `dist/meowcam-bridge/meowcam-bridge.exe` (Windows) or standalone folder.
4. Zip the `dist/meowcam-bridge` folder for distribution.

**Blockers before PyInstaller:**
- Bridge core UDP implementation must be complete and tested.
- Decide on log file vs console output for windowed mode.
- Test on a real Windows machine (firewall prompts, port binding).

## Versioning

- Version lives in `pyproject.toml` and `src/meowcam_bridge/__init__.py`.
- Git tags: `v0.1.0`, `v0.2.0`, etc.
- Packaged releases: GitHub Releases with `.zip` of PyInstaller build.

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

## Checklist for releasing v0.2 (first packaged version)

- [ ] Bridge core UDP relay implemented and tested.
- [ ] UI tabs: Settings, Presets, Manual Control, Diagnostics all functional.
- [ ] `launch.bat` tested on Windows 10/11.
- [ ] `launch.sh` tested on Ubuntu 22.04+.
- [ ] PyInstaller `.exe` builds successfully.
- [ ] Firewall behaviour documented and tested.
- [ ] README and SETUP.md reviewed by non-technical reader.
