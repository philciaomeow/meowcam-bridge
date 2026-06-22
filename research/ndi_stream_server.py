"""NDI video receiver with MJPEG HTTP streaming via FastAPI.

Research prototype for MeowCam Bridge — demonstrates:
  1. NDI source discovery and frame capture using ndi-python
  2. Frame conversion (BGRX/BGRA → JPEG) via OpenCV
  3. MJPEG over HTTP via FastAPI StreamingResponse

Dependencies:
    pip install ndi-python opencv-python fastapi uvicorn

NDI SDK:
    Windows: download from https://ndi.video/for-developers/ndi-sdk/
    The ndi-python PyPI wheels for Windows x64 (Python 3.10–3.14) bundle the
    runtime DLLs, so end-users do NOT need the full SDK installed.

Usage:
    python ndi_stream_server.py --source "MY_NDI_SOURCE" --port 8080

    Then open http://localhost:8080/video.mjpg in a browser or VLC.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import threading
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import uvicorn

# ndi-python imports the compiled NDIlib module
import NDIlib as ndi


# ---------------------------------------------------------------------------
# Thread-safe NDI frame grabber
# ---------------------------------------------------------------------------

class NDIFrameGrabber:
    """Discover an NDI source, connect, and serve the latest frame."""

    def __init__(self, source_name: str | None = None, color_format: int | None = None):
        """
        Args:
            source_name: Exact NDI source name (from find). If None, connects
                         to the first source discovered.
            color_format: One of ndi.RECV_COLOR_FORMAT_* constants.
                          Defaults to BGRX_BGRA for easy OpenCV display.
        """
        self.source_name = source_name
        self.color_format = color_format or ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        self._ndi_recv = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None  # BGR image (H, W, 3)
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return

        if not ndi.initialize():
            raise RuntimeError("NDIlib.initialize() failed")

        # --- discover sources ------------------------------------------------
        ndi_find = ndi.find_create_v2()
        if ndi_find is None:
            raise RuntimeError("ndi.find_create_v2() failed")

        sources: list = []
        timeout_ms = 5000  # wait up to 5 s for sources
        waited = 0
        while not sources and waited < timeout_ms:
            ndi.find_wait_for_sources(ndi_find, 1000)
            sources = ndi.find_get_current_sources(ndi_find)
            waited += 1000
            print(f"[NDI] Looking for sources … found {len(sources)}")

        if not sources:
            ndi.find_destroy(ndi_find)
            ndi.destroy()
            raise RuntimeError("No NDI sources found on the network")

        # Pick source: by name or first available
        if self.source_name:
            matches = [s for s in sources if self.source_name in s.ndi_name]
            source = matches[0] if matches else sources[0]
        else:
            source = sources[0]

        print(f"[NDI] Connecting to source: {source.ndi_name}")

        # --- create receiver -------------------------------------------------
        recv_create = ndi.RecvCreateV3()
        recv_create.color_format = self.color_format
        # recv_create.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST  # default

        self._ndi_recv = ndi.recv_create_v3(recv_create)
        if self._ndi_recv is None:
            ndi.find_destroy(ndi_find)
            ndi.destroy()
            raise RuntimeError("ndi.recv_create_v3() failed")

        ndi.recv_connect(self._ndi_recv, source)
        ndi.find_destroy(ndi_find)

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print("[NDI] Capture loop started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._ndi_recv:
            ndi.recv_destroy(self._ndi_recv)
            self._ndi_recv = None
        ndi.destroy()
        print("[NDI] Stopped")

    # ------------------------------------------------------------------
    # Capture thread
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Runs in background thread — captures frames and stores latest."""
        while self._running:
            # 5000 ms timeout per frame
            t, video_frame, _, _ = ndi.recv_capture_v2(
                self._ndi_recv, 5000, want_audio=False, want_metadata=False
            )

            if t == ndi.FRAME_TYPE_VIDEO and video_frame is not None:
                # video_frame.data is a numpy array.
                # For BGRX_BGRA format: shape (H, W, 4), dtype uint8.
                # We drop the alpha/X channel for JPEG encoding.
                try:
                    frame = np.copy(video_frame.data)
                    if frame.ndim == 3 and frame.shape[2] == 4:
                        # BGRX → BGR (drop last channel)
                        frame = frame[:, :, :3]
                    with self._lock:
                        self._latest_frame = frame
                finally:
                    ndi.recv_free_video_v2(self._ndi_recv, video_frame)

            # Small sleep to avoid spinning when no frames
            # (recv_capture_v2 already blocks up to 5 s, so this is minimal)

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def get_jpeg(self, quality: int = 85) -> bytes | None:
        """Return the latest frame as JPEG bytes, or None if no frame yet."""
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return None
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return encoded.tobytes() if ok else None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

grabber: NDIFrameGrabber | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global grabber
    grabber = NDIFrameGrabber(source_name=args.source)
    grabber.start()
    yield
    grabber.stop()


app = FastAPI(title="NDI MJPEG Stream", lifespan=lifespan)


@app.get("/")
async def root() -> dict:
    return {
        "service": "NDI → MJPEG stream",
        "endpoints": {
            "/video.mjpg": "MJPEG stream (multipart/x-mixed-replace)",
            "/snapshot.jpg": "Single JPEG frame",
        },
    }


@app.get("/snapshot.jpg")
async def snapshot() -> StreamingResponse:
    jpeg = grabber.get_jpeg() if grabber else None
    if jpeg is None:
        # Return a 1x1 grey placeholder so the client doesn't break
        placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
        _, jpeg = cv2.imencode(".jpg", placeholder)
        jpeg = jpeg.tobytes()
    return StreamingResponse(io.BytesIO(jpeg), media_type="image/jpeg")


async def _mjpeg_generator() -> AsyncGenerator[bytes, None]:
    """Yield multipart JPEG frames for MJPEG streaming."""
    boundary = b"--frame\r\n"
    header = b"Content-Type: image/jpeg\r\n\r\n"
    while True:
        jpeg = grabber.get_jpeg() if grabber else None
        if jpeg:
            yield boundary + header + jpeg + b"\r\n"
        # Target ~30 fps throttle; adjust as needed
        await asyncio.sleep(1 / 30)


@app.get("/video.mjpg")
async def video_feed() -> StreamingResponse:
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NDI → MJPEG HTTP streamer")
    parser.add_argument("--source", default=None, help="NDI source name substring")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality (0-100)")
    args = parser.parse_args()

    # Pass quality to grabber via global — simplistic but works for prototype
    # (In production, use dependency injection or app.state)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
