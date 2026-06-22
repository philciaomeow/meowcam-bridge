# NDI Receive + MJPEG Web Streaming — Research Findings

> Task: t_e6ac5b2c | Researcher: raven | Branch: develop-v0.2

---

## 1. NDI Python Library: `ndi-python` (buresu/ndi-python)

### Installation

```bash
pip install ndi-python
```

- **PyPI provides prebuilt wheels** for Windows x64, macOS arm64, and Linux x64/aarch64/armv7l.
- **Python versions:** 3.10 – 3.14 (confirmed on PyPI page).
- **Windows:** the wheel bundles the NDI runtime DLLs (`Processing.NDI.Lib.x64.dll` or equivalent). End-users **do NOT need the full NDI SDK installed** if installing from PyPI on a supported platform.
- **Linux:** requires `avahi-daemon` for source discovery (`sudo apt install avahi-daemon`).
- **Build from source:** only needed if the wheel doesn't match your platform. Requires CMake + NDI SDK downloaded from https://ndi.video/for-developers/ndi-sdk/

### Key API (receive path)

```python
import NDIlib as ndi

ndi.initialize()                           # one-time init
ndi_find = ndi.find_create_v2()            # source discovery
sources = ndi.find_get_current_sources(ndi_find)

recv_create = ndi.RecvCreateV3()
recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
ndi_recv = ndi.recv_create_v3(recv_create)
ndi.recv_connect(ndi_recv, sources[0])

# Capture loop
t, v, a, _ = ndi.recv_capture_v2(ndi_recv, 5000,
                                 want_audio=False, want_metadata=False)
if t == ndi.FRAME_TYPE_VIDEO:
    frame = np.copy(v.data)                # numpy array, shape (H, W, 4) uint8
    ndi.recv_free_video_v2(ndi_recv, v)    # MUST free
```

### Frame Data Format

| `color_format` | Shape | Channels | Notes |
|---|---|---|---|
| `RECV_COLOR_FORMAT_BGRX_BGRA` | (H, W, 4) | BGR + alpha/X | Easiest for OpenCV |
| `RECV_COLOR_FORMAT_RGBX_RGBA` | (H, W, 4) | RGB + alpha/X |  |
| `RECV_COLOR_FORMAT_FASTEST` | (H, W, 2) | UYVY | Best perf, needs YUV→RGB conversion |
| `RECV_COLOR_FORMAT_BEST` | varies | P216 / PA16 | 16-bit, highest quality |

For MJPEG streaming, `BGRX_BGRA` is the pragmatic choice — drop the 4th channel and feed straight to `cv2.imencode('.jpg', ...)`.

### Memory / Performance Notes

- `np.copy(v.data)` is required because `v.data` may point to an internal buffer reused by the SDK.
- `ndi.recv_free_video_v2()` must be called after processing to avoid leaking frames.
- The capture call is thread-safe; can have separate threads for video and audio.

---

## 2. FastAPI MJPEG Streaming Pattern

Standard pattern (used by both prototypes):

```python
from fastapi.responses import StreamingResponse

async def gen_frames():
    while True:
        jpeg = grabber.get_jpeg()
        if jpeg:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n')
        await asyncio.sleep(1 / target_fps)

@app.get("/video.mjpg")
async def video_feed():
    return StreamingResponse(
        gen_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
```

- `multipart/x-mixed-replace` is the HTTP standard for MJPEG.
- Each part is a JPEG image preceded by `--frame` boundary.
- Browsers (`<img src="/video.mjpg">`), VLC, and most IP-camera viewers support this natively.

### Threading Model

- NDI/OpenCV capture runs in a **dedicated background thread** (blocking I/O).
- The async generator yields frames to FastAPI's event loop.
- A `threading.Lock()` protects the latest-frame buffer so the capture thread and HTTP workers don't race.

---

## 3. NDI SDK Installation Requirements on Windows

| Scenario | SDK Required? | Notes |
|---|---|---|
| `pip install ndi-python` on supported Windows x64 | **No** | Wheel bundles runtime DLLs |
| Build from source / unsupported Python | **Yes** | Download SDK from ndi.video |
| Redistribute PyInstaller app | **No** | Bundle the DLL that ships with the wheel |

The NDI SDK EULA allows redistribution of the runtime libraries for applications that use NDI. This is the standard approach for commercial NDI tools.

---

## 4. Frame Rate / Latency

### NDI Official Specs

| Resolution | FPS | Typical Bandwidth | Latency |
|---|---|---|---|
| 1080p | 60 | ~150 Mbps | ~16 ms (1 frame) |
| 1080p | 30 | ~100 Mbps | ~33 ms (1 frame) |
| 4Kp60 | 60 | ~300 Mbps | ~16 ms |

- NDI is designed for **sub-frame latency** (~1 frame glass-to-glass under ideal conditions).
- Real-world latency includes network buffering, capture pipeline, and display pipeline. Expect **20–60 ms** total for a local gigabit network.
- The MJPEG HTTP layer adds its own buffering. For lowest latency, keep JPEG quality moderate (70–85) and target 30 fps.

### USB/HDMI Capture Card (OpenCV fallback)

- Latency depends entirely on the capture hardware. Cheap USB 3.0 HDMI capture cards typically add **50–150 ms**.
- OpenCV `VideoCapture` with `CAP_DSHOW` on Windows is usually stable but may default to 30 fps.
- No network latency — entirely local.

---

## 5. Bandwidth on Local Network

| Stream Type | 1080p30 | 1080p60 | 4Kp60 |
|---|---|---|---|
| NDI (full) | ~100 Mbps | ~150 Mbps | ~300 Mbps |
| NDI|HX (H.264) | ~10–20 Mbps | ~20–40 Mbps | ~40–80 Mbps |
| MJPEG @ 85% quality | ~30–50 Mbps | ~60–100 Mbps | ~150–250 Mbps |

- NDI full-bandwidth requires a **gigabit network** for multiple streams.
- NDI|HX is the low-bandwidth variant (H.264 compressed). Requires H.264 decode capability. On Linux, ndi-python dynamically loads FFmpeg — ensure matching `libavcodec` version.
- The MJPEG HTTP stream is a **second hop** (NDI → decode → re-encode JPEG → HTTP). It does NOT replace NDI on the wire; it's for browser viewing.

---

## 6. PyInstaller Onedir Bundling of NDI DLLs

### What needs bundling

The `ndi-python` wheel ships with:
- `_NDIlib*.pyd` (the C extension, PyInstaller usually detects this)
- `Processing.NDI.Lib.x64.dll` or similar (the NDI runtime DLL)

### PyInstaller spec additions

```python
# In the Analysis() call:
hiddenimports=['NDIlib'],

# Add the NDI DLL as a binary:
binaries=[
    ('path/to/Processing.NDI.Lib.x64.dll', '.'),
],

# Or use --add-binary CLI flag:
# pyinstaller --add-binary "path/to/Processing.NDI.Lib.x64.dll;." ...
```

### Runtime DLL search path

If the DLL isn't found at runtime, add a runtime hook:

```python
# runtime_hook_ndi.py
import os
import sys

# PyInstaller onedir: _MEIPASS points to the extraction folder
if hasattr(sys, '_MEIPASS'):
    ndi_dll_dir = sys._MEIPASS
    os.add_dll_directory(ndi_dll_dir)  # Windows 8.1+
```

Then in the spec:
```python
runtime_hooks=['runtime_hook_ndi.py']
```

### Key findings

1. **PyInstaller does NOT automatically collect non-Python DLLs** unless they are dependencies detected by dependency walking. The NDI runtime DLL may need explicit `--add-binary`.
2. **`hiddenimports=['NDIlib']`** is recommended because the module is a C extension imported dynamically.
3. **Test the onedir build on a clean Windows VM** without NDI SDK installed to verify the DLL is bundled correctly.
4. The NDI SDK license permits redistribution of runtime libraries in this manner.

---

## 7. OpenCV USB/HDMI Capture Fallback

### When to use

- No NDI source available (e.g. camera has only HDMI out).
- Simpler deployment (no NDI runtime concerns).
- Lower CPU overhead (no NDI discovery / network stack).

### Device enumeration

```python
cv2.VideoCapture(index, cv2.CAP_DSHOW)  # Windows DirectShow
```

- Indices are 0, 1, 2, … but enumeration can be slow on Windows (MSMF backend probes each device).
- Use `cv2.CAP_DSHOW` on Windows for better compatibility with USB capture cards.

### Resolution / format control

```python
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 30)
```

Not all capture cards honor these; check actual values with `cap.get(...)` after opening.

---

## 8. Delivered Code Artifacts

| File | Purpose |
|---|---|
| `research/ndi_stream_server.py` | Full NDI receive → JPEG → FastAPI MJPEG stream |
| `research/usb_stream_server.py` | OpenCV USB/HDMI capture → JPEG → FastAPI MJPEG stream |
| `research/NDI_RESEARCH.md` | This document |

Both servers expose:
- `GET /video.mjpg` — live MJPEG stream
- `GET /snapshot.jpg` — single JPEG frame
- `GET /` — service info

The USB server also exposes `GET /devices` to list working capture indices.

---

## 9. Recommendations for MeowCam Bridge Integration

1. **Make video streaming optional** — add `video_enabled` flag per route so the bridge works without video hardware.
2. **Support both NDI and USB sources** — auto-detect NDI sources first, fall back to USB device index if none found.
3. **Run video capture in a subprocess or thread** — keep it isolated from the UDP bridge loop to avoid jitter on PTZ commands.
4. **JPEG quality configurable** — default 85, let user tune for bandwidth vs. quality.
5. **PyInstaller testing checklist:**
   - [ ] Build onedir with `--add-binary` for NDI DLL
   - [ ] Test on Windows machine without NDI SDK
   - [ ] Verify `hiddenimports=['NDIlib']` in spec
   - [ ] Include runtime hook for `os.add_dll_directory`

---

*End of research findings.*
