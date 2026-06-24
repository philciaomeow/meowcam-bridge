# MeowCam Bridge

A standalone onsite application that bridges a PTZOptics PT-JOY-G4 controller to Sony BRC-H900 cameras with BRBK-IP10 IP cards. Designed for non-technical operators at live events, studios, or installs. No internet, no cloud, no Docker, no k3s required.

Made by the **meow team** as a MeowWorks onsite starter app.

## What it does

- Listens for VISCA-over-UDP packets from a PTZ controller on per-camera incoming ports.
- Decodes raw VISCA (generic VISCA(UDP) mode) or Sony-framed VISCA from the controller.
- Translates to Sony VISCA-over-IP framing with correct address byte (0x81) and sequence numbers.
- Sends to the camera from the fixed UDP source port the BRBK-IP10 expects (52381).
- Receives camera replies and maps them back to the controller.
- Injects **preset drive speed** before preset recalls so Slow/Medium/Fast affects preset travel, not just manual pan/tilt.
- Translates controller OSD Enter/Back to the direct BRC-H900 menu commands that actually work.
- **Captures live video** from NDI sources or USB/HDMI capture cards for in-app preview.
- **ATEM switcher integration** for PGM/PVW tally indicators and SuperSource quadrant routing.
- Provides a local web UI with live preview, presets, manual control, diagnostics, and settings.

## What's new in v0.2

| Feature | Details |
|---------|---------|
| **NDI video capture** | Receive NDI streams from network sources (ATEM, OBS, vMix, etc.) with auto-discovery |
| **USB/HDMI capture** | Connect USB capture cards (Blackmagic, Elgato, etc.) for direct camera feeds |
| **Shared USB device** | Multiple camera routes share ONE capture device with different crop regions — e.g. ATEM multiview split into 4 quadrants |
| **Crop presets** | Quick-select buttons: Full Frame, Top-Left, Top-Right, Bottom-Left, Bottom-Right, or Custom |
| **Live preview grid** | 2×2 camera preview grid with click-to-enlarge, showing real video feeds |
| **LIVE indicator** | Red border for PGM (program), green border for PVW (preview) via ATEM tally |
| **ATEM integration** | SuperSource 2×2 quadrant mapping, PGM/PVW tally, AUX routing |
| **System tray app** | Windows tray icon with "Open Control Surface" and "Close Server" — no command window |
| **Preset thumbnails** | Capture a snapshot of the current camera view when saving a preset. Shown as 50% opacity background image on the preset button with number, name, and speed indicator overlaid |
| **Per-preset speed** | Each preset stores its own slow/medium/fast speed, applied when recalling. Speed indicators (›/››/›››) shown on buttons |
| **Preset range toggle** | Split 16 presets into two pages: 1–8 and 9–16. Toggle button in toolbar on both preset and manual pages |
| **Manual page v2 layout** | Video preview left, PTZ/lens/OSD controls right (2×2 grid), preset buttons along bottom. Auto-sizes to fill viewport |
| **Save Mode** | Toggle on manual page to switch preset buttons between recall (normal) and save (red highlight). Click a preset in save mode to open a dialog with name, speed, and snapshot capture options |
| **Busy state locking** | Per-camera busy locking prevents crash when clicking a second preset while camera is still moving. Buttons show ⏳ and pulse while busy. Auto-clears when camera sends completion reply |
| **Auto-sizing layout** | CSS flex layout fills the browser viewport without scrollbars. Adapts to any screen resolution. Preset buttons use 16:10 aspect ratio to match camera previews |

## Key features

| Feature | Details |
|---------|---------|
| **Multi-camera** | Up to 8 cameras via shared camera socket on port 52381, replies routed by source IP |
| **Controller profiles** | PT-JOY-G4 VISCA(UDP) with custom ports, or Sony VISCA(UDP) on fixed 52381 |
| **Preset speed** | Slow/Medium/Fast per camera, applied to both manual movement and preset recall |
| **OSD menu** | Controller Enter/Back translated to working BRC-H900 direct menu commands |
| **Web UI** | Live preview, touch-friendly preset quadrants, manual PTZ/lens/OSD controls, diagnostics, settings |
| **Standalone** | No internet, no cloud, no Docker. Just Python and a browser |
| **Windows launcher** | Double-click `MeowCamBridge.exe` — system tray app with loading screen, no Python install needed |

## Quick start (development)

```bash
# Install (editable dev install with video deps)
pip install -e ".[dev]"

# Run with default config (auto-created)
python -m meowcam_bridge

# Run with specific config
python -m meowcam_bridge --config my-config.json
```

Open `http://localhost:8080` in a browser.

## Quick start (onsite / Windows)

1. Unzip the MeowCam Bridge folder.
2. Double-click `MeowCamBridge.exe`.
3. A loading screen appears, then the system tray icon shows "MeowCam Bridge".
4. Allow through Windows Firewall when prompted.
5. Browser opens to `http://localhost:8080`.
6. Go to **Settings**, enter your camera IPs, click **Save**.
7. Go to **Preview** to see live video, or **Presets** / **Manual Control** to operate cameras.

See `SETUP.md` for the full onsite guide.

## Video sources

MeowCam Bridge supports three video source types per camera route:

| Source | Description | Linux | Windows |
|--------|-------------|-------|---------|
| **NDI** | Receive NDI streams from ATEM, OBS, vMix, NewTek Connect | ✅ (needs avahi-daemon + mDNS route) | ✅ (native mDNS — no workaround needed) |
| **USB Capture** | HDMI capture cards via USB (Blackmagic, Elgato, etc.) | ✅ (V4L2 backend) | ✅ (DirectShow/MediaFoundation backend) |
| **Test Pattern** | Generated colour bars / gradient for testing | ✅ | ✅ |

### Shared USB capture

Multiple camera routes can share ONE USB capture device with different crop regions. This is ideal for splitting an ATEM multiview output into individual camera previews:

```
ATEM Multiview (USB capture)
├── Camera 1: Top-Left quadrant crop (0, 0, 0.5, 0.5)
├── Camera 2: Top-Right quadrant crop (0.5, 0, 0.5, 0.5)
├── Camera 3: Bottom-Left quadrant crop (0, 0.5, 0.5, 0.5)
└── Camera 4: Bottom-Right quadrant crop (0.5, 0.5, 0.5, 0.5)
```

### Crop/Region presets

Each camera has quick-select crop buttons:

- **Full Frame** — entire video source
- **Top-Left / Top-Right / Bottom-Left / Bottom-Right** — quarter-crops for multiview splitting
- **Custom** — manual crop region (future: visual drag-to-select)

## Architecture

```
Controller (UDP)  →  Bridge Listener (per-route port)
                         ↓
                  Input Profile (decode)
                         ↓
                  Bridge Core (route + state + seq rewrite + preset speed injection)
                         ↓
                  Output Profile (encode + force address + OSD translation)
                         ↓
Camera (UDP 52381)  ←  Shared socket (source port 52381)
Camera replies (UDP 65000) → Shared socket → Route by source IP → Controller

Video Source (NDI/USB)  →  Video Manager (shared device pool + crop)
                         ↓
                  MJPEG Stream → Web UI Preview Grid

ATEM Switcher (TCP)  →  ATEM Module (tally + SuperSource)
                         ↓
                  PGM/PVW state → Web UI LIVE indicators
```

- **Profiles** are separate from core routing. Input profiles decode controller packets; output profiles encode camera packets. New profiles can be added without changing the bridge core.
- **Routes** define up to 8 camera mappings: incoming port, controller profile, camera IP/port, camera profile, and video source config.
- **Sequence numbers** are managed per-route so the camera sees clean incrementing values independent of whatever the controller sends.
- **Preset speed** is injected as a BRC-H900 PRESET DRIVE SPEED command (`81 01 7E 01 0B pp qq FF`) before each preset recall. Internal command replies are consumed by the bridge, not forwarded to the controller.
- **Video Manager** manages video sources per-route, with a shared device pool for USB capture so multiple routes can read from one physical device simultaneously.
- **Web UI** is static HTML/CSS/JS served by FastAPI. No build step, no bundler, no internet CDN.

## Socket modes

| Mode | When | How |
|------|------|-----|
| **Single-socket** | `incoming_port == source_port` (both 52381) | One socket handles both controller and camera traffic, filtered by source address |
| **Two-socket shared** | Multiple cameras share source port 52381 | ONE shared camera socket, per-route controller listeners, replies routed by camera source IP |

## Repository layout

```
meowcam-bridge/
  pyproject.toml
  README.md
  CHANGELOG.md           # Version history and fix log
  PROJECT_STATE.md       # Current project status, known issues, architecture notes
  SETUP.md              # Onsite operator guide
  PACKAGING.md          # Developer packaging guide
  build_windows.py      # One-command Windows .exe build
  launch.bat            # Windows double-click launcher
  launch.sh             # Linux/macOS launcher
  meowcam-bridge.spec   # PyInstaller spec (tray app + NDI DLLs + OpenCV)
  hooks/
    runtime_hook_ndi.py # NDI DLL runtime path hook
  src/meowcam_bridge/
    __init__.py
    __main__.py          # python -m meowcam_bridge entry point
    app.py               # FastAPI + uvicorn entry point
    config.py            # Pydantic models, JSON load/save
    bridge.py            # Async UDP relay, route state, diagnostics, command dispatch
    video.py             # Video sources: NDI, USB (shared), test pattern + crop
    video_manager.py     # Per-route video source lifecycle + config change detection
    atem.py              # ATEM switcher integration (SuperSource, tally)
    tray_app.py          # Windows system tray app (pystray + tkinter loading)
    protocols/
      base.py                    # InputProfile / OutputProfile ABCs
      visca.py                   # VISCA framing utilities
      visca_commands.py          # Shared VISCA payload builders
      input_ptzoptics.py         # PT-JOY-G4 Sony VISCA UDP profile
      input_ptzoptics_visca_udp.py  # PT-JOY-G4 generic VISCA(UDP) profile (custom ports)
      output_sony_brbk.py        # Sony BRC-H900 / BRBK-IP10 profile
    web/
      index.html
      app.js
      styles.css
  tests/
    test_config.py
    test_visca.py
    test_sony_brbk.py
    test_bridge_mapping.py
    test_input_ptzoptics.py
    test_api.py
    test_video.py
  examples/
    config.example.json
```

## Configuration

Config is a JSON file (auto-created as `meowcam-bridge.json` if missing). Up to 8 `routes` are supported. Each route has:

- `enabled` — whether the bridge listens for this route
- `label` — human-readable name (shown in UI)
- `incoming_port` — UDP port the bridge listens on for controller packets
- `input_profile` — controller profile name
- `output_profile` — camera profile name
- `camera_ip`, `camera_port` — target camera address
- `movement_speed` — `slow`, `medium`, or `fast` (persists to config)
- `preset_labels` — up to 16 preset names
- `preset_speeds` — up to 16 per-preset speed overrides (`slow`/`medium`/`fast`/empty)
- `preset_thumbs` — up to 16 per-preset thumbnail data URLs (small JPEG snapshots)
- `video` — video source config: `enabled`, `source_type` (`ndi`/`usb`/`testpattern`), `source_name` (NDI), `usb_device_index` (USB), `resolution`, crop fields (`crop_x`/`crop_y`/`crop_w`/`crop_h` as 0.0–1.0 fractions)

## Profiles

| Type | Name | Description |
|------|------|-------------|
| Input | `ptzoptics_pt_joy_g4_visca_udp` | PTZOptics PT-JOY-G4 generic VISCA(UDP) with custom ports (recommended for multi-camera) |
| Input | `ptzoptics_pt_joy_g4_sony_visca_udp` | PTZOptics PT-JOY-G4 Sony VISCA(UDP) on fixed port 52381 (single camera only) |
| Output | `sony_brc_h900_brbk_ip10` | Sony BRC-H900 with BRBK-IP10 (fixed source port 52381, reply port 65000, address 0x81) |

## Development

```bash
# Run tests
pytest

# Lint (optional)
ruff check src tests
```

## BRC-H900 VISCA reference

Key commands confirmed by live testing against BRC-H900 with BRBK-IP10 firmware v2.10:

| Command | Payload (after 0x81) | Notes |
|---------|----------------------|-------|
| Pan/Tilt | `01 06 01 VV WW DD DD FF` | VV=pan 1-18, WW=tilt 1-17 |
| Zoom In | `01 04 07 2Z FF` | Z=speed 1-7 |
| Preset Recall | `01 04 3F 02 NN FF` | N=1-16, no speed byte |
| Preset Drive Speed | `01 7E 01 0B pp qq FF` | pp=preset-1, qq=speed 1-18 |
| Menu Open | `01 06 06 02 FF` | Works |
| Menu Enter | `01 7E 01 02 00 01 FF` | Direct command (documented Select ACKs but doesn't enter) |
| Menu Back | `01 7E 01 02 00 02 FF` | Direct command |

## Versioning

- Version lives in `pyproject.toml` and `src/meowcam_bridge/__init__.py`.
- Git tags: `v0.1.0`, `v0.1.1`, `v0.2.0`, etc.
- Packaged releases: GitHub Releases with `.zip` of build output.

## License

MIT
