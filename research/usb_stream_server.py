"""USB/HDMI capture fallback — OpenCV VideoCapture with MJPEG HTTP streaming.

This is the simpler fallback path when NDI is unavailable or undesirable.
Uses any DirectShow/V4L2 capture device (webcam, HDMI USB capture card, etc.)
and serves frames as an MJPEG stream over HTTP via FastAPI.

Dependencies:
    pip install opencv-python fastapi uvicorn

Usage:
    python usb_stream_server.py --device 0 --port 8080

    Then open http://localhost:8080/video.mjpg in a browser.
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


# ---------------------------------------------------------------------------
# Thread-safe OpenCV frame grabber
# ---------------------------------------------------------------------------

class USBFrameGrabber:
    """Wrap cv2.VideoCapture with thread-safe latest-frame access."""

    def __init__(self, device: int | str = 0, backend: int | None = None):
        """
        Args:
            device: Camera index (int) or URL string (e.g. RTSP).
            backend: cv2.CAP_* backend. On Windows, cv2.CAP_DSHOW is often
                     more reliable for USB capture cards than the default MSMF.
        """
        self.device = device
        self.backend = backend
        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return

        if self.backend is not None:
            self._cap = cv2.VideoCapture(self.device, self.backend)
        else:
            self._cap = cv2.VideoCapture(self.device)

        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open capture device: {self.device}")

        # Log device properties
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        print(f"[USB] Opened device {self.device} — {width}x{height} @ {fps:.1f} fps")

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        print("[USB] Stopped")

    # ------------------------------------------------------------------
    # Capture thread
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._lock:
                    self._latest_frame = frame
            # No sleep needed — cap.read() blocks for next frame

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def get_jpeg(self, quality: int = 85) -> bytes | None:
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return None
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return encoded.tobytes() if ok else None


# ---------------------------------------------------------------------------
# Device enumeration helpers
# ---------------------------------------------------------------------------

def list_capture_devices(max_index: int = 10) -> list[dict]:
    """Try opening camera indices 0..max_index and report working ones.

    Note: on Windows this can be slow because MSMF tries to initialise each
    device. Use sparingly (e.g. at startup or on user request).
    """
    devices = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            devices.append({"index": i, "width": width, "height": height})
        cap.release()
    return devices


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

grabber: USBFrameGrabber | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global grabber
    backend = None
    if sys.platform == "win32":
        backend = cv2.CAP_DSHOW
    grabber = USBFrameGrabber(device=args.device, backend=backend)
    grabber.start()
    yield
    grabber.stop()


app = FastAPI(title="USB MJPEG Stream", lifespan=lifespan)


@app.get("/")
async def root() -> dict:
    return {
        "service": "USB/HDMI → MJPEG stream",
        "endpoints": {
            "/video.mjpg": "MJPEG stream (multipart/x-mixed-replace)",
            "/snapshot.jpg": "Single JPEG frame",
            "/devices": "List available capture devices (slow on Windows)",
        },
    }


@app.get("/devices")
async def devices() -> list[dict]:
    return list_capture_devices()


@app.get("/snapshot.jpg")
async def snapshot() -> StreamingResponse:
    jpeg = grabber.get_jpeg() if grabber else None
    if jpeg is None:
        placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
        _, jpeg = cv2.imencode(".jpg", placeholder)
        jpeg = jpeg.tobytes()
    return StreamingResponse(io.BytesIO(jpeg), media_type="image/jpeg")


async def _mjpeg_generator() -> AsyncGenerator[bytes, None]:
    boundary = b"--frame\r\n"
    header = b"Content-Type: image/jpeg\r\n\r\n"
    while True:
        jpeg = grabber.get_jpeg() if grabber else None
        if jpeg:
            yield boundary + header + jpeg + b"\r\n"
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
    parser = argparse.ArgumentParser(description="USB/HDMI → MJPEG HTTP streamer")
    parser.add_argument("--device", type=int, default=0, help="Capture device index")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality (0-100)")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
