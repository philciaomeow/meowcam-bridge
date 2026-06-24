# Changelog

All notable changes to MeowCam Bridge are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [v0.2.0-dev] — 2026-06-23 (develop-v0.2 branch)

### Added
- **NDI video capture** — receive NDI streams from ATEM, OBS, vMix with auto-discovery
- **USB/HDMI capture** — connect USB capture cards (Blackmagic, Elgato) for direct camera feeds
- **Shared USB device** — multiple camera routes share ONE capture device with different crop regions
- **Crop presets** — Full Frame, Top-Left, Top-Right, Bottom-Left, Bottom-Right, Custom
- **Live preview grid** — 2×2 camera preview with click-to-enlarge
- **ATEM integration** — SuperSource 2×2 routing, PGM/PVW tally overlays
- **System tray app** — Windows tray icon with "Open Control Surface" and "Close Server"
- **Preset thumbnails** — capture camera snapshot on save, display as 50% opacity overlay on preset buttons
- **Per-preset speed** — slow/medium/fast stored per preset (not just per camera)
- **Preset range toggle** — switch between presets 1-8 and 9-16 on both preset and manual pages
- **Manual page v2 layout** — video left, controls right (2×2 grid), presets along bottom
- **Save Mode** — toggle to switch preset buttons between recall and save mode on manual page
- **Modal dialog** — replaces browser `prompt()` for preset naming and save configuration
- **Speed indicators** — › (slow), ›› (medium), ››› (fast) shown on preset buttons
- **Auto-sizing layout** — CSS flex layout fills viewport without scrollbars, adapts to browser resolution
- **Busy state polling** — frontend polls server every 2s to auto-clear busy flags when camera finishes moving
- `preset_thumbs` field added to `CameraRoute` config model

### Fixed
- **Windows WSAECONNRESET crash** — offline camera (Camera 2) sent ICMP port unreachable → `ConnectionResetError` flooded asyncio event loop → 30s UI freeze. Fixed with `SIO_UDP_CONNRESET` ioctl + error suppression in `send()` and `error_received()`
- **Concurrent preset crash** — VISCA sequence number collision when second preset sent while first still in progress. Fixed with per-route busy locking (`is_route_busy`, `_release_busy`), 15s auto-expire safety valve
- **Shared-socket drain** — sequence error handler was draining ALL camera packets from shared UDP socket, not just the erroring camera. Removed drain.
- **Speed injection race** — `create_task` (non-blocking) fired speed command after preset recall → sequence collision. Changed to `await` with try/except wrapper
- **`isPresetRecall is not defined`** — `const isPresetRecall` declared inside `try{}` block but referenced in `finally{}`. JavaScript block scope made it invisible. Moved declaration above `try`
- **`routeBusy is not defined`** — JS variable used but never declared. Added `const routeBusy = {}` and `updatePresetButtonsBusy()` function
- **Video going black on preset click** — `renderPresets()` called on every click, which did `grid.innerHTML = ''` destroying MJPEG `<img>` elements. Replaced with direct DOM highlight update
- **Preset buttons stay grayed** — `finally` block threw scope error before `setTimeout` could clear busy state. Fixed by the `isPresetRecall` scope fix
- **Thumbnails lost on page switch** — `normaliseRoutes()` didn't preserve `preset_thumbs` from API response. Added preservation of `preset_thumbs` and `preset_speeds` in `normaliseRoutes`, `cloneDefaultRoute`, `DEFAULT_ROUTE`, and `readRouteBlock`
- **500 error on preset save** — speed injection `create_task` could fail silently. Wrapped in try/except
- **NDIlib double-free on Linux** — `recv_free_video_v2()` triggers `free(): double free detected in tcache 2` → SIGABRT. Workaround: skip `recv_free_video_v2`, use `np.array()` for frame deep copy
- **NDI discovery on Linux** — requires avahi-daemon + multicast route (`sudo ip route add 224.0.0.0/4 dev <interface>`)
- **Windows USB device names** — now show friendly names (pygrabber on Windows, v4l2-ctl on Linux)
- **Static asset cache** — version query parameter on CSS/JS URLs forces browser refresh

### Known Issues
- **Video preview twitching** — brief black screen flash (10-25ms) every 10-15 seconds. Likely MJPEG reconnection or browser resource constraint. Pending testing on higher-spec hardware.
- **Windows Session 0 tray app** — `wmic process call create` launches into Session 0 where tkinter can't create a window. Use `start_bridge.bat` or start via RDP/desktop.

## [v0.1.1] — 2026-06-21 (main branch, frozen)

### Added
- VISCA bridge: PTZOptics PT-JOY-G4 → Sony BRC-H900/BRBK-IP10
- Multi-camera support (up to 8) via shared UDP socket on port 52381
- Per-camera speed presets (slow/medium/fast)
- OSD menu translation (controller Enter/Back → BRC-H900 direct menu commands)
- Web UI: preview, presets, manual control, diagnostics, settings
- Windows .exe build via PyInstaller with system tray app
- 100 tests passing

### VISCA Protocol Notes
- Sony BRC-H900 VISCA-over-IP packet format: bytes 0-1 = message type, bytes 2-3 = payload length (big-endian uint16), bytes 4-7 = sequence number (big-endian uint32), bytes 8+ = payload
- BRBK-IP10 replies from source port 65000 to destination port 52381
- Controller must listen on UDP port 52381 for replies
- Preset drive speed injected via `0x01 0x7E 0x01 0x0B <preset-1> <speed> 0xFF` before recall
- OSD menu: `8101060602FF` (on), `8101060603FF` (off), direct enter `81017E01020001FF`, direct back `81017E01020002FF`