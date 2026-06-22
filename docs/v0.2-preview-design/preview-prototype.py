"""Minimal FastAPI prototype of /api/video/feed/{index} for v0.2.

Smoke-tests the *browser-facing contract* end-to-end against mockup.html /
video-client.js WITHOUT any real camera hardware. It serves a synthetic JPEG
test-pattern stream over multipart/x-mixed-replace, exactly the shape the real
bridge will use once per-route video sources (NDI / RTSP / MJPEG device) are
wired behind the same `frame_iter()` abstraction.

Run (from the meowcam-bridge repo root, with the venv active):
    python docs/v0.2-preview-design/preview-prototype.py
    # then open http://localhost:8080/  (serves a demo page embedding the feed)

If port 8080 is already taken by the real bridge, override by editing the
uvicorn.run() line at the bottom, or run the prototype instead of the bridge.

To point the real web UI at it, replace a pane's content with:
    <img src="/api/video/feed/0?fps=8&quality=60&width=480">
(or use video-client.js -> new MjpegImgView(pane, 0)).

Requires: fastapi, uvicorn, Pillow (PIL). Pillow is the only new dep; the real
bridge will additionally need an upstream decoder (ndi-python / av / etc.) but
those sit BEHIND frame_iter and do not touch this contract.
"""
from __future__ import annotations

import asyncio
import io
import time
from typing import AsyncIterator

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, StreamingResponse
    import uvicorn
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        f"Missing dependency: {exc.name}. Install with: pip install fastapi uvicorn Pillow"
    ) from exc

try:
    from PIL import Image, ImageDraw  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Pillow is required for the test pattern: pip install Pillow"
    ) from exc

app = FastAPI(title="MeowCam Bridge v0.2 preview prototype")

BOUNDARY = b"meowframe"


def _render_test_pattern(cam_index: int, width: int, height: int, t: float) -> bytes:
    """Synthetic moving test-pattern JPEG for one camera, at time t (seconds)."""
    hue = (cam_index * 67) % 360
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # vertical gradient via horizontal strips (cheap, no numpy)
    r1 = (int(40 + 30 * (0.5 + 0.5 * (hue / 360 - 0.5))),
          int(20 + 20 * ((hue + 40) % 360 / 360)),
          int(30 + 20 * ((hue + 80) % 360 / 360)))
    r2 = (min(255, r1[0] + 40), min(255, r1[1] + 30), min(255, r1[2] + 20))
    for y in range(0, height, 4):
        mix = y / height
        c = (int(r1[0] * (1 - mix) + r2[0] * mix),
             int(r1[1] * (1 - mix) + r2[1] * mix),
             int(r1[2] * (1 - mix) + r2[2] * mix))
        draw.rectangle([0, y, width, y + 4], fill=c)

    # grid
    grid = (255, 255, 255)
    for x in range(0, width, 40):
        draw.line([x, 0, x, height], fill=grid, width=1)
    for y in range(0, height, 40):
        draw.line([0, y, width, y], fill=grid, width=1)

    # moving crosshair to suggest live motion
    cx = width / 2 + (width * 0.18) * (0.5 + 0.5 * (cam_index % 3) / 3) * (lambda v: v)(1) * 1
    import math
    cx = width / 2 + 120 * math.sin(t * 2 + cam_index)
    cy = height / 2 + 60 * math.cos(t * 1.4 + cam_index)
    draw.line([cx - 30, cy, cx + 30, cy], fill=(255, 184, 108), width=2)
    draw.line([cx, cy - 30, cx, cy + 30], fill=(255, 184, 108), width=2)
    draw.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], outline=(255, 184, 108), width=2)

    # watermark
    draw.text((14, 12), f"CAM {cam_index + 1}", fill=(255, 255, 255))
    draw.text((14, 32), time.strftime("%H:%M:%S"), fill=(200, 200, 200))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


async def frame_iter(
    cam_index: int, fps: int, quality: int, width: int
) -> AsyncIterator[bytes]:
    """Async generator yielding JPEG bytes at the requested fps.

    In the real bridge this is the per-route abstraction: it pulls decoded
    frames from whatever upstream the route is configured for (NDI|HX via
    ndi-python, an RTSP URL via PyAV, or a direct MJPEG device) and JPEG-
    encodes each. The browser-facing contract (this generator's output) is
    identical regardless of upstream.
    """
    height = max(1, int(width * 9 / 16))
    interval = 1.0 / max(1, min(fps, 30))
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        jpeg = _render_test_pattern(cam_index, width, height, t)
        yield jpeg
        await asyncio.sleep(interval)


@app.get("/api/video/feed/{cam_index}")
async def video_feed(cam_index: int, fps: int = 8, quality: int = 60, width: int = 480):
    """MJPEG stream — the v0.2 baseline browser contract.

    Content-Type is multipart/x-mixed-replace; browsers render it in a plain
    <img src="/api/video/feed/0">. Query params are hints; clamp defensively.
    """
    fps = max(1, min(fps, 30))
    quality = max(10, min(quality, 95))
    width = max(160, min(width, 1280))

    async def generate() -> AsyncIterator[bytes]:
        async for jpeg in frame_iter(cam_index, fps, quality, width):
            yield (
                b"--" + BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )

    return StreamingResponse(
        generate(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (
        "<html><body style='font-family:sans-serif;background:#0b1020;color:#eee;padding:2rem'>"
        "<h1>🐾 MeowCam v0.2 preview prototype</h1>"
        "<p>MJPEG feed is live. Try:</p>"
        "<ul>"
        "<li><code>/api/video/feed/0?fps=8&quality=60&width=480</code></li>"
        "<li><code>/api/video/feed/1</code></li>"
        "</ul>"
        "<p>Or open <code>mockup.html</code> from the same folder.</p>"
        "<img src='/api/video/feed/0?fps=8&width=480' style='width:480px;border-radius:12px'>"
        "</body></html>"
    )


if __name__ == "__main__":
    # Default 8080 matches the project convention. Pick another port if the real
    # bridge is already running on this host (see docstring).
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
