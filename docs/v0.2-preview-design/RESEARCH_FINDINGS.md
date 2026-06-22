# MeowCam Bridge v0.2 — Video Preview Research Findings

**Task:** Research embedding an MJPEG stream (`/api/video/feed`) into the web UI,
design a Preview tab (2×2 camera grid + preset columns), a Manual tab single-pane
preview, and a restructured Settings tab. Deliver mockups + transport findings.

**Date:** 2026-06-22
**Repo state:** `develop-v0.2` worktree. Today the bridge only does VISCA/IP control
(UDP 52381/65000). No video transport exists yet — this document scopes how it should.

---

## 1. Camera reality check (what we're actually displaying)

The deployed cameras are **Sony BRC-H900 + BRBK-IP10** IP cards. Important hardware
fact that drives every decision below:

- The **BRBK-IP10** provides **VISCA/IP remote control** over UDP 52381/65000 and
  RM-IP discovery. It is a *control* interface.
- The **BRC-H900 body** outputs video over **HD-SDI / composite**. The H900 generation
  (unlike the newer BRC-X400 / BRC-X1000 / SRG series) does **not** stream video over
  IP natively and has **no built-in NDI**.
- Therefore a "camera preview" in the browser implies an **intermediate IP encoder**
  on each camera's SDI output — e.g. a Magewell/Birddog NDI encoder, an RTSP encoder,
  or an HD-SDI→MJPEG edge device. **The bridge does not own the raw camera pixels.**

This is the single most important scoping fact: **`/api/video/feed` will be a proxy /
transcode hop**, not a direct camera tap. The bridge's job is to normalise whatever
per-camera IP stream exists (NDI|HX, RTSP, raw MPEG-TS, or an already-MJPEG device)
into one consistent browser-facing format, and to add a control surface (on/off,
resolution, fps) to the Settings tab.

**Recommendation:** treat the upstream video source as a configurable per-route input
profile (mirroring the existing input/output profile pattern in `protocols/`), and have
the bridge expose a single normalised MJPEG `/api/video/feed/{index}` for the browser.
Keep NDI→MJPEG transcode behind a `video_source` config field so non-NDI sites (plain
RTSP encoders, direct MJPEG cameras) work with zero code change.

---

## 2. Transport options compared

### 2.1 MJPEG over `multipart/x-mixed-replace` in `<img>` ✅ (recommended baseline)

The browser receives a never-ending HTTP response with
`Content-Type: multipart/x-mixed-replace; boundary=...`, each part being a full JPEG.
Point an `<img>` at the URL and frames replace automatically.

| Aspect | Detail |
|--------|--------|
| Browser support | Chrome, Edge, Firefox: solid for **image** content-type (Chrome long ago dropped `x-mixed-replace` for non-image types, but kept it for images). **Safari: unreliable** — historically needs `window.stop()` / iframe tricks to tear down the connection; behaviour varies by version. For a kiosk/touch panel locked to one browser this is a non-issue (lock to Chromium). |
| Latency | Tied to frame rate + JPEG encode + TCP. Typical 150–400 ms end-to-end for 5–10 fps preview. **Not real-time** but fine for framing/aim. |
| Bandwidth | High — each frame is a full JPEG, no inter-frame compression. ~0.5–2 Mbps per camera at 640×360/8fps. 4 cameras ≈ 2–8 Mbps on the LAN. Acceptable on gigabit; watch WiFi tablets. |
| JS complexity | ~5 lines. `<img src="/api/video/feed/0">`. No canvas, no decoder. |
| Auth / headers | **Cannot set custom headers on `<img src>`.** Auth must be cookie-based, path-token (`?token=`), or same-origin only. The bridge is local/trusted so this is fine. |
| **Concurrent connection cap** ⚠️ | Browsers allow ~6 simultaneous connections **per origin** (HTTP/1.1). A 2×2 grid = 4 open MJPEG connections + the page's own XHR = right at the ceiling. A 5th–8th camera or a second tab stalls. **This is the main architectural risk of MJPEG-in-`<img>`.** Mitigations below. |
| Connection lifecycle | One persistent TCP per stream; no per-frame ack. Browser shows no `onload` per frame (Chrome does not fire `onload` on replace parts), so you **cannot** detect a stalled/frozen stream from JS easily — must use a heartbeat. |
| Reconnect | On disconnect the `<img>` just freezes silently. Need a JS watchdog that reloads `img.src` (append `?t=<ts>`) when a heartbeat stalls. |

**Mitigations for the connection cap (pick one):**

1. **HTTP/2** — multiplexes many streams over one TCP connection, sidesteps the 6-stream
   cap. Uvicorn supports HTTP/2 behind a TLS + `--http h2` config, but it adds cert
   complexity for a local app. Overkill for now.
2. **Lazy-load previews** — only mount the `<img>` for the 4 cameras on the *current*
   page (Preview tab page 0 = cams 1–4, page 1 = cams 5–8). The existing preset paging
   already does exactly this. **Recommended:** never have >4 MJPEG `<img>` alive at once.
3. **Single MJPEG mux** — one `/api/video/feed` connection that tiles all 4 cameras into
   one JPEG (server-side compose). Cuts connections to 1 but loses per-camera framing
   and is more server work. Keep as a fallback if HTTP/1.1 limits bite.

### 2.2 WebSocket binary frames (controlled MJPEG) — good upgrade path

Server pushes JPEG frames as WebSocket binary messages; client draws each onto a
`<canvas>` (or `createImageBitmap` → canvas for speed).

| Aspect | Detail |
|--------|--------|
| Browser support | Universal (WS is older than MJPEG-replace). |
| Latency | Same JPEG cost as 2.1, but **one WS connection can carry all cameras** (demux by a small header), so the 6-connection cap disappears. |
| Control | Full duplex: client can send `{"camera":2,"fps":4,"quality":60}` to throttle per pane; server can send frame metadata (timestamp, dropped-count). |
| Stall detection | Trivial — if no frame in N ms, show "reconnecting" overlay and the server auto-throttles. |
| JS complexity | Moderate — ~60–100 lines of WS + canvas draw loop. |
| Cost vs `<img>` | More code, but removes the hardest MJPEG-in-`<img>` problems (cap, stall detection, per-frame control). |

### 2.3 WebRTC — lowest latency, highest cost

H.264/VP8 over SRTP with a WHIP/WHEP signalling step. Sub-second latency, hardware
decode, tiny bandwidth.

| Aspect | Detail |
|--------|--------|
| Latency | <500 ms typical. Real motion-quality preview. |
| Complexity | **High.** Needs an SFU or a WHIP/WHEP endpoint, ICE/STUN, a real H.264 encode pipeline (GStreamer/ffmpeg/MediaMTX). Far more moving parts than MJPEG. |
| Fit | Overkill for a framing/aim preview on a closed LAN. Worth revisiting only if the preview becomes the *program* monitor or operators complain MJPEG is too laggy for fast moves. |

### 2.4 Recommendation for v0.2

> **Ship MJPEG-in-`<img>` as the v0.2 baseline** (zero-JS, works today with a
> `StreamingResponse` in FastAPI), with the connection-cap mitigated by lazy-loading
> only the visible page's 4 cameras. **Design the JS client and the `/api/video/feed`
> URL scheme so a WebSocket upgrade is a drop-in later** — same per-camera index path,
> same heartbeat overlay, same throttle semantics. Do **not** attempt WebRTC in v0.2.

Rationale: the bridge is a *control* tool; the preview is for framing and confirming a
preset landed, not for colour-critical monitoring. 150–400 ms MJPEG latency is
acceptable for that. The simplicity win (a single FastAPI `StreamingResponse` +
`<img>`) is worth far more than sub-second latency for this use case.

---

## 3. Browser compatibility summary

| Browser | `<img>` MJPEG | WS frames | WebRTC | Notes |
|---------|:---:|:---:|:---:|-------|
| Chrome / Edge (Chromium) | ✅ | ✅ | ✅ | **Recommended kiosk target.** Lock the touch panel here. |
| Firefox | ✅ | ✅ | ✅ | Solid. |
| Safari (desktop + iOS) | ⚠️ flaky | ✅ | ✅ | `x-mixed-replace` tears down badly; may need WS path if Safari must be supported. |
| Onsite touch panels | — | — | — | Typically locked Chromium/Edge kiosk → MJPEG baseline is safe. |

**Action:** document "preview optimised for Chromium kiosk" in SETUP.md; if a panel
runs Safari, fall back to the WebSocket client.

---

## 4. `/api/video/feed` endpoint design (proposed)

```
GET /api/video/feed/{route_index}
  ?fps=8        # cap frames/sec (default from route config)
  &quality=60   # JPEG quality 1-95
  &width=480    # downscale width (height keeps aspect)
Query params are hints; server may clamp to route-configured maxima.
Response: Content-Type: multipart/x-mixed-replace; boundary=meowframe
```

Implementation sketch (FastAPI):

```python
from fastapi.responses import StreamingResponse

@app.get("/api/video/feed/{route_index}")
async def video_feed(route_index: int, fps: int = 8, quality: int = 60, width: int = 480):
    async def generate():
        boundary = b"--meowframe\r\n"
        async for jpeg in _bridge.frame_iter(route_index, fps=fps, quality=quality, width=width):
            yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: "
            yield str(len(jpeg)).encode()
            yield b"\r\n\r\n"
            yield jpeg
            yield b"\r\n"
    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=meowframe")
```

`_bridge.frame_iter()` is the per-route video source abstraction: it pulls decoded
frames from whatever upstream the route is configured for (NDI|HX via the NDI SDK /
`ndi-python`, an RTSP URL via `av`/PyAV, or a direct MJPEG device) and JPEG-encodes
each at the requested size. This keeps the browser-facing contract constant while the
upstream can be any source — exactly mirroring how `InputProfile`/`OutputProfile`
already decouple controller protocol from bridge core.

**Concurrency note:** each open `/api/video/feed` connection holds one streaming
generator + one upstream decode loop. Cap to the 4 visible cameras; pause the
off-screen page's feeds (the JS tears down `<img>` by clearing `src` when paging).

---

## 5. Config model additions (proposed `CameraRoute` fields)

Extend `CameraRoute` in `config.py` with a `video` sub-block so the Settings tab can
expose Camera Video Setup without touching control fields:

```python
class CameraVideo(BaseModel):
    enabled: bool = False
    source_type: Literal["none", "ndi", "rtsp", "mjpeg_url"] = "none"
    ndi_source_name: str = ""          # exact NDI source, e.g. "CAM1 (Birddog)"
    rtsp_url: str = ""                 # e.g. rtsp://192.168.51.50/stream
    mjpeg_url: str = ""                # direct MJPEG device URL
    resolution: Literal["320x180","480x270","640x360","960x540"] = "640x360"
    frame_rate: int = Field(default=8, ge=1, le=30)
    jpeg_quality: int = Field(default=60, ge=10, le=95)
```

This is additive — existing configs without a `video` block default to `enabled=False`
and the preview panes show a "no video configured" placeholder. Backwards-compatible.

---

## 6. Recommended tab architecture (answers the 3 layout asks)

1. **Preview tab (new)** — 2×2 grid of the current page's 4 cameras. Each cell = video
   pane on top, that camera's preset buttons below (reusing the exact preset-button
   component from the Presets tab so naming/edit/last-used behaviour is identical).
   Preset page buttons (Cameras 1–4 / 5–8) sit in the toolbar and only the visible 4
   video feeds are live — this is also the connection-cap mitigation. See mockup.

2. **Manual tab** — add a single large video pane to the existing manual layout, showing
   the currently-selected camera (driven by `#manual-camera-select`). Video follows the
   dropdown. Existing PTZ/lens/OSD/preset-tool cards move to the right column; video
   takes the left/hero position so the operator can see the shot while jogging.

3. **Settings tab** — split into two cards per route (or two grouped sections):
   **Camera Control Setup** (the existing enabled/label/incoming-port/profile/IP/port
   fields) and **Camera Video Setup** (the new `CameraVideo` block: source type, NDI
   source name / RTSP URL, resolution, frame rate, JPEG quality, video-enable toggle).
   Keeps control and video concerns visually separated as the task requested.

---

## 7. Risks / open questions for the build task

- **NDI packaging IS viable in v0.2.** Earlier concern that native NDI SDK DLLs
  would complicate the PyInstaller build is resolved by concurrent research in
  `research/NDI_RESEARCH.md` (task t_e6ac5b2c): the `ndi-python` PyPI wheel
  **bundles the NDI runtime DLL** on Windows x64 / macOS arm64 / Linux x64, so
  `pip install ndi-python` needs **no separate SDK install**, and PyInstaller
  onedir can collect the DLL via `hiddenimports=['NDIlib']` + `--add-binary`.
  NDI should therefore be the **primary** video source for v0.2, with RTSP /
  MJPEG-URL as alternative `source_type` values (see §5).
- **Endpoint name reconciliation.** The `research/` prototypes (raven) expose
  `/video.mjpg` + `/snapshot.jpg`; this design uses `/api/video/feed/{route_index}`.
  Pick the route-indexed `/api/video/feed/{index}` as the **canonical** path in the
  integrated bridge (matches the existing `/api/*` convention and the per-camera
  model), and have the build task port the NDI/OpenCV capture loop from
  `research/ndi_stream_server.py` behind the `frame_iter()` abstraction in §4.
- **Decode CPU:** 4 concurrent decodes + JPEG re-encodes can pin a low-spec onsite
  CPU. Keep default fps modest (8) and resolution 640×360; expose fps/quality in
  Settings so an operator can trade down on a weak box.
- **Threading isolation:** run NDI/OpenCV capture in a dedicated background thread
  or subprocess (per raven's §9 recommendation) so video work never starves the
  UDP PTZ relay loop on the same process.
- **No upstream yet:** since no camera has a configured IP video source today, the
  mockup ships with a built-in **synthetic test-pattern generator** so the UI can be
  developed and demoed without any camera hardware. `preview-prototype.py` proves
  the `/api/video/feed` contract end-to-end; the real `_bridge.frame_iter` (backed
  by raven's NDI capture loop) swaps in behind the same URL.

---

## 8. Deliverables in this folder

| File | Purpose |
|------|---------|
| `RESEARCH_FINDINGS.md` | This document. |
| `mockup.html` | Standalone, self-contained mockup of all three layouts (Preview / Manual / Settings) using the existing MeowCam palette. Renders in any browser, no server needed. Includes a JS test-pattern "video feed" so the grid shows live motion. |
| `video-client.js` | Reference implementation of the browser video client: MJPEG `<img>` loader with a watchdog/stall-detection overlay, plus the WebSocket-frame variant as a commented upgrade path. |
| `preview-prototype.py` | Minimal FastAPI `/api/video/feed/{index}` with a synthetic JPEG test-pattern generator, so the real endpoint shape can be smoke-tested end-to-end against `mockup.html`. |
