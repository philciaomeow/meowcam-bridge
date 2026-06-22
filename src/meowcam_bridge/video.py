"""Video capture sources for MeowCam Bridge.

Provides a pluggable video source abstraction with implementations for:
  - NDI receive (via ndi-python)
  - USB/HDMI capture (via OpenCV VideoCapture)
  - Synthetic test pattern (no hardware required)

All sources run capture in a dedicated background thread with a
threading.Lock-protected frame buffer.  The consumer API is synchronous
(get_jpeg) so it can be called from both sync and async contexts.
"""

from __future__ import annotations

import abc
import asyncio
import io
import sys
import threading
import time
from typing import AsyncGenerator

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class VideoSource(abc.ABC):
    """Abstract video source.  Implementations must be thread-safe."""

    def __init__(self, route_label: str = "Camera") -> None:
        self.route_label = route_label
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background capture thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background capture thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------
    # Capture loop (implemented by subclasses)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def _capture_loop(self) -> None:
        """Background thread entry point.  Must check ``self._running``."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def get_jpeg(self, quality: int = 85, width: int | None = None) -> bytes | None:
        """Return the latest frame as JPEG bytes, optionally resized.

        Args:
            quality: JPEG quality (0-100).
            width: If given, resize so that the frame width matches *width*
                (height is scaled proportionally).
        """
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return None

        if width is not None and width > 0 and frame.shape[1] != width:
            h, w = frame.shape[:2]
            new_h = int(h * (width / w))
            frame = cv2.resize(frame, (width, new_h), interpolation=cv2.INTER_AREA)

        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return encoded.tobytes() if ok else None

    def get_frame(self) -> np.ndarray | None:
        """Return the latest raw frame (BGR) or None."""
        with self._lock:
            return self._latest_frame

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _store_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest_frame = frame


# ---------------------------------------------------------------------------
# Test pattern — always works, no hardware required
# ---------------------------------------------------------------------------

class TestPatternSource(VideoSource):
    """Synthetic test pattern generator.

    Produces a coloured gradient with the camera label and a live timestamp.
    Useful for verifying the UI pipeline when no video hardware is present.
    """

    def __init__(self, route_label: str = "Camera", resolution: str = "640x360") -> None:
        super().__init__(route_label)
        self._w, self._h = self._parse_resolution(resolution)

    @staticmethod
    def _parse_resolution(resolution: str) -> tuple[int, int]:
        w, h = resolution.split("x")
        return int(w), int(h)

    def _capture_loop(self) -> None:
        while self._running:
            frame = self._render_frame()
            self._store_frame(frame)
            time.sleep(1 / 30)  # 30 fps synthetic

    def _render_frame(self) -> np.ndarray:
        w, h = self._w, self._h
        # Horizontal colour gradient (BGR)
        gradient = np.zeros((h, w, 3), dtype=np.uint8)
        for x in range(w):
            gradient[:, x] = [
                int(255 * (x / w)),          # B
                int(128 + 127 * ((w - x) / w)),  # G
                int(255 * ((w - x) / w)),    # R
            ]

        # Add label text
        label = self.route_label
        cv2.putText(
            gradient, label, (20, h // 2),
            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA,
        )

        # Add timestamp
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(
            gradient, ts, (20, h - 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA,
        )

        return gradient


# ---------------------------------------------------------------------------
# NDI receiver
# ---------------------------------------------------------------------------

class NDISource(VideoSource):
    """Receive video from an NDI source and convert frames to JPEG.

    Requires ``ndi-python`` (``NDIlib``) to be installed.  If the import
    fails the constructor raises *ImportError* so the caller can fall back
    to another source.
    """

    def __init__(
        self,
        source_name: str | None = None,
        route_label: str = "Camera",
    ) -> None:
        super().__init__(route_label)
        self.source_name = source_name
        try:
            import NDIlib as _ndi  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "ndi-python is required for NDI capture. "
                "Install it with: pip install ndi-python"
            ) from exc
        self._ndi = _ndi
        self._ndi_recv = None

    def start(self) -> None:
        if self._running:
            return
        if not self._ndi.initialize():
            raise RuntimeError("NDIlib.initialize() failed")

        # Discover sources
        ndi_find = self._ndi.find_create_v2()
        if ndi_find is None:
            self._ndi.destroy()
            raise RuntimeError("ndi.find_create_v2() failed")

        sources: list = []
        timeout_ms = 5000
        waited = 0
        while not sources and waited < timeout_ms:
            self._ndi.find_wait_for_sources(ndi_find, 1000)
            sources = self._ndi.find_get_current_sources(ndi_find)
            waited += 1000

        if not sources:
            self._ndi.find_destroy(ndi_find)
            self._ndi.destroy()
            raise RuntimeError("No NDI sources found on the network")

        if self.source_name:
            matches = [s for s in sources if self.source_name in s.ndi_name]
            source = matches[0] if matches else sources[0]
        else:
            source = sources[0]

        recv_create = self._ndi.RecvCreateV3()
        recv_create.color_format = self._ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        self._ndi_recv = self._ndi.recv_create_v3(recv_create)
        if self._ndi_recv is None:
            self._ndi.find_destroy(ndi_find)
            self._ndi.destroy()
            raise RuntimeError("ndi.recv_create_v3() failed")

        self._ndi.recv_connect(self._ndi_recv, source)
        self._ndi.find_destroy(ndi_find)
        super().start()

    def stop(self) -> None:
        super().stop()
        if self._ndi_recv:
            self._ndi.recv_destroy(self._ndi_recv)
            self._ndi_recv = None
        self._ndi.destroy()

    def _capture_loop(self) -> None:
        while self._running:
            t, video_frame, _, _ = self._ndi.recv_capture_v2(
                self._ndi_recv, 5000, want_audio=False, want_metadata=False
            )
            if t == self._ndi.FRAME_TYPE_VIDEO and video_frame is not None:
                try:
                    frame = np.copy(video_frame.data)
                    if frame.ndim == 3 and frame.shape[2] == 4:
                        frame = frame[:, :, :3]
                    self._store_frame(frame)
                finally:
                    self._ndi.recv_free_video_v2(self._ndi_recv, video_frame)


# ---------------------------------------------------------------------------
# USB / HDMI capture
# ---------------------------------------------------------------------------

class USBCaptureSource(VideoSource):
    """OpenCV VideoCapture wrapper for USB/HDMI capture cards."""

    def __init__(
        self,
        device: int = 0,
        backend: int | None = None,
        route_label: str = "Camera",
    ) -> None:
        super().__init__(route_label)
        self.device = device
        self.backend = backend
        self._cap: cv2.VideoCapture | None = None

    def start(self) -> None:
        if self._running:
            return
        if self.backend is not None:
            self._cap = cv2.VideoCapture(self.device, self.backend)
        else:
            self._cap = cv2.VideoCapture(self.device)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open capture device: {self.device}")
        super().start()

    def stop(self) -> None:
        super().stop()
        if self._cap:
            self._cap.release()
            self._cap = None

    def _capture_loop(self) -> None:
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                self._store_frame(frame)
            # cap.read() blocks for the next frame; no sleep needed


# ---------------------------------------------------------------------------
# MJPEG streaming helpers
# ---------------------------------------------------------------------------

async def mjpeg_generator(
    source: VideoSource,
    fps: float = 8.0,
    quality: int = 60,
    width: int = 480,
) -> AsyncGenerator[bytes, None]:
    """Yield multipart JPEG frames for FastAPI StreamingResponse."""
    boundary = b"--frame\r\n"
    header = b"Content-Type: image/jpeg\r\n\r\n"
    sleep_interval = 1.0 / max(fps, 0.1)
    while True:
        jpeg = source.get_jpeg(quality=quality, width=width)
        if jpeg:
            yield boundary + header + jpeg + b"\r\n"
        await asyncio.sleep(sleep_interval)


def snapshot_response(source: VideoSource, quality: int = 60, width: int = 480) -> tuple[bytes, str]:
    """Return (jpeg_bytes, media_type) for a single snapshot.

    If no frame is available a grey placeholder is returned so the client
    doesn't break.
    """
    jpeg = source.get_jpeg(quality=quality, width=width)
    if jpeg is None:
        placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
        _, encoded = cv2.imencode(".jpg", placeholder)
        jpeg = encoded.tobytes()
    return jpeg, "image/jpeg"
