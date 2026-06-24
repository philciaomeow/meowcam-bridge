# MeowCam Bridge — Project State

**Last updated:** 2026-06-23  
**Branch:** `develop-v0.2`  
**Status:** Beta — feature-complete for single-camera operation, pending multi-camera testing

## Current State

MeowCam Bridge has evolved from a background VISCA packet bridge into a full PTZ camera control application with live video preview, preset management, thumbnail snapshots, and a touch-friendly web UI.

### What's Working
- ✅ VISCA bridge: PTZOptics PT-JOY-G4 → Sony BRC-H900/BRBK-IP10
- ✅ Live video preview (NDI, USB capture, test pattern)
- ✅ Preset recall with per-preset speed (slow/medium/fast)
- ✅ Preset save with name, speed, and snapshot thumbnail
- ✅ Preset thumbnail overlays (50% opacity camera snapshot on buttons)
- ✅ 4-column preset page with 1-8/9-16 toggle
- ✅ Manual control page (video left, controls right 2×2, presets bottom)
- ✅ Per-route busy locking (prevents crash on rapid preset clicks)
- ✅ Windows WSAECONNRESET crash fix (Camera 2 offline no longer kills bridge)
- ✅ Auto-sizing layout (fills viewport without scrollbars)
- ✅ System tray app (Windows)
- ✅ ATEM tally integration (PGM/PVW overlays)
- ✅ Shared USB capture with crop regions
- ✅ 169 tests passing

### Known Issues
- **Video preview twitching:** Brief black screen flash (10-25ms) every 10-15 seconds. Likely MJPEG stream reconnection or browser resource constraint on low-spec viewing device. Pending testing on work media server/laptop.
- **Camera 2 offline:** Commands sent silently, no errors. Expected behaviour — camera not connected.
- **Windows VM bridge start:** `wmic process call create` launches into Session 0 (no desktop). Tray app's tkinter loading screen can't create a window there. Workaround: use `start_bridge.bat` (uvicorn directly) or start via RDP/desktop.

### Pending Testing
- Multi-camera preview (need 2+ cameras from work)
- Video preview twitching on higher-spec hardware
- Windows .exe rebuild with v0.2 features (NDI, USB, thumbnails)
- ATEM SuperSource integration with real switcher

## Architecture

```
PTZOptics PT-JOY-G4 ──UDP VISCA──▶ Bridge (Python/FastAPI)
                                      │
                                      ├──▶ Sony BRC-H900 (VISCA-over-IP)
                                      │
                                      ├──▶ Video capture (NDI / USB / test)
                                      │     └──▶ MJPEG stream → browser <img>
                                      │
                                      └──▶ Web UI (http://0.0.0.0:8080)
                                            ├── Preview tab (2×2 grid)
                                            ├── Presets tab (4-column, 1-8/9-16 toggle)
                                            ├── Manual tab (video + controls + presets)
                                            ├── Diagnostics tab
                                            └── Settings tab
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Per-route busy locking | Cameras operate independently — clicking preset on Camera 1 shouldn't block Camera 2 |
| Await speed injection | Non-blocking `create_task` caused race condition — speed arrived after recall. Await with try/except ensures correct order |
| SIO_UDP_CONNRESET on Windows | Microsoft-recommended fix for WinError 10054 flooding asyncio event loop when offline camera sends ICMP port unreachable |
| Removed shared-socket drain | Sequence error handler was draining ALL camera packets, not just the erroring one |
| MJPEG over WebSocket/canvas | Browser `<img>` with multipart/x-mixed-replace is simplest reliable approach. Chromium doesn't fire onload on multipart parts — poll `naturalWidth` instead |
| Preset thumbnails as data URLs | Small (160px wide, quality 50) JPEGs stored in config JSON. No separate file system needed |
| 2×4 preset button grid | Matches camera preview aspect ratio, buttons are large and touch-friendly |

## Commit History (develop-v0.2, recent)

| Commit | Description |
|--------|-------------|
| `656671b` | Preset buttons: taller (16:10 aspect ratio) to match camera preview |
| `3ecdbe5` | Preset buttons: 2×4 grid (bigger buttons) instead of 4×2 |
| `4f91f73` | Fix: thumbnails persist across page switches + auto-sizing layout |
| `e999a3c` | 4-col preset layout with 1-8/9-16 toggle, manual v2 layout, preset thumbnails |
| `dc195fe` | Fix isPresetRecall scope, video black on preset click, new preset layout |
| `594089e` | Fix: declare routeBusy variable and add updatePresetButtonsBusy function |
| `511127a` | Fix 500 error: wrap preset speed injection in try/except |
| `15093d4` | Fix preset crash: await speed injection, per-preset speed, manual preset grid with save mode |
| `53f8c62` | Merge: resolve app.py conflict (keep busy-locking version) |
| `28d4a9f` | Fix: per-route busy locking to prevent crash on rapid preset recall |
| `92c040b` | Fix: Windows WSAECONNRESET crash on preset recall + non-blocking speed injection |

## Environments

| Environment | IP | Role |
|-------------|-----|------|
| Dev VM | 172.21.8.51 | Source repo, testing, 169 tests |
| Windows VM | 172.21.11.126 | Onsite bridge testing, camera network 192.168.51.x |
| Camera 1 | 192.168.51.123 | Sony BRC-H900 (online) |
| Camera 2 | 192.168.51.124 | Sony BRC-H900 (offline) |

## Files Modified in This Session

| File | Changes |
|------|---------|
| `config.py` | Added `preset_thumbs` field to CameraRoute model |
| `web/index.html` | New preset tab (4-col + range toggle), new manual tab layout, version bumps |
| `web/app.js` | renderPresets 4-column, renderManualPresets v2, snapshot capture, thumbnail overlay, busy state fixes, preset_thumbs persistence in normaliseRoutes |
| `web/styles.css` | Auto-sizing flex layout, 2×4 preset grid, 16:10 button aspect ratio, thumbnail overlay styles, manual v2 layout, responsive breakpoints |