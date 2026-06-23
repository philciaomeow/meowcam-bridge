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
import logging
import sys
import threading
import time
from typing import AsyncGenerator

import cv2
import numpy as np

logger = logging.getLogger(__name__)

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
        # Crop region (fractions of frame dimensions). All zeros = no crop.
        self._crop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def set_crop(self, x: float, y: float, w: float, h: float) -> None:
        """Set region-of-interest crop. Values are fractions 0.0-1.0.

        x, y: top-left corner (fraction of width/height)
        w, h: width/height of crop region (fraction of full width/height)
        All zeros = no crop (full frame).
        """
        self._crop = (max(0.0, x), max(0.0, y), max(0.0, w), max(0.0, h))

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
        """Return the latest frame as JPEG bytes, optionally resized and cropped.

        Args:
            quality: JPEG quality (0-100).
            width: If given, resize so that the frame width matches *width*
                (height is scaled proportionally).
        """
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return None

        # Apply crop if configured (crop_w > 0 means crop is active)
        cx, cy, cw, ch = self._crop
        if cw > 0 and ch > 0:
            h, w = frame.shape[:2]
            x0 = int(w * cx)
            y0 = int(h * cy)
            x1 = min(w, int(w * (cx + cw)))
            y1 = min(h, int(h * (cy + ch)))
            if x1 > x0 and y1 > y0:
                frame = frame[y0:y1, x0:x1]

        # Resize only if frame is LARGER than requested width.
        # Never upscale cropped regions — that causes the zoomed-in look.
        if width is not None and width > 0 and frame.shape[1] > width:
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

    # Module-level NDI init guard — NDIlib crashes with double-free if
    # initialize()/destroy() is called multiple times.
    _ndi_init_count = 0
    _ndi_init_lock = threading.Lock()

    @classmethod
    def _ensure_ndi_init(cls, ndi_module):
        """Initialize NDIlib once globally. Never destroyed until process exits."""
        with cls._ndi_init_lock:
            if cls._ndi_init_count == 0:
                if not ndi_module.initialize():
                    raise RuntimeError("NDIlib.initialize() failed")
            cls._ndi_init_count += 1

    @classmethod
    def _release_ndi(cls, ndi_module):
        """Release NDIlib reference. Never actually destroy — NDIlib has
        a known double-free bug when destroy() is called."""
        with cls._ndi_init_lock:
            cls._ndi_init_count = max(0, cls._ndi_init_count - 1)

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
        self._ensure_ndi_init(self._ndi)

        # Discover sources
        ndi_find = self._ndi.find_create_v2()
        if ndi_find is None:
            raise RuntimeError("ndi.find_create_v2() failed")

        sources: list = []
        timeout_ms = 5000
        waited = 0
        while not sources and waited < timeout_ms:
            self._ndi.find_wait_for_sources(ndi_find, 500)
            sources = self._ndi.find_get_current_sources(ndi_find)
            waited += 500

        if not sources:
            self._ndi.find_destroy(ndi_find)
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
            raise RuntimeError("ndi.recv_create_v3() failed")

        self._ndi.recv_connect(self._ndi_recv, source)
        self._ndi.find_destroy(ndi_find)
        super().start()

    def stop(self) -> None:
        super().stop()
        # Note: we intentionally do NOT call recv_destroy or ndi.destroy()
        # NDIlib v6 has a known double-free bug on Linux that causes crashes
        # at cleanup. The OS will reclaim resources on process exit.
        self._ndi_recv = None

    def _capture_loop(self) -> None:
        while self._running:
            t, video_frame, _, _ = self._ndi.recv_capture_v2(
                self._ndi_recv, 1000, want_audio=False, want_metadata=False
            )
            if t == self._ndi.FRAME_TYPE_VIDEO and video_frame is not None:
                try:
                    # NDI returns a numpy array wrapping its internal buffer.
                    # We must copy the data before NDI reuses the buffer.
                    # Using np.array() creates a true deep copy.
                    src = video_frame.data  # shape (H, W, 4) for BGRX_BGRA
                    frame = np.array(src, dtype=np.uint8)  # deep copy
                    if frame.ndim == 3 and frame.shape[2] == 4:
                        frame = frame[:, :, :3]  # drop alpha -> BGR
                    self._store_frame(frame)
                except Exception as exc:
                    logger.warning("NDI frame copy error: %s", exc)
                # Note: we intentionally skip recv_free_video_v2 — calling it
                # triggers a double-free bug in NDIlib v6 on Linux. NDIlib
                # manages its own internal buffer pool and will reuse buffers
                # on the next recv_capture_v2 call.
                # self._ndi.recv_free_video_v2(self._ndi_recv, video_frame)


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

# ---------------------------------------------------------------------------
# Shared USB capture (multiple routes can share one device)
# ---------------------------------------------------------------------------

class _SharedUSBPool:
    """Singleton pool of shared USB capture devices."""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._devices = {}
        return cls._instance
    
    def get_device(self, device_index: int, backend: int | None = None):
        key = (device_index, backend)
        if key not in self._devices:
            cap = cv2.VideoCapture(device_index, backend or cv2.CAP_V4L2)
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open USB capture device: {device_index}")
            self._devices[key] = {
                'cap': cap,
                'lock': threading.Lock(),
                'frame': None,
                'refs': 0,
                'thread': None,
                'running': False,
            }
            self._start_capture(key)
        self._devices[key]['refs'] += 1
        return self._devices[key]
    
    def _start_capture(self, key):
        dev = self._devices[key]
        dev['running'] = True
        def capture_loop():
            while dev['running']:
                ret, frame = dev['cap'].read()
                if ret:
                    with dev['lock']:
                        dev['frame'] = frame.copy()
        dev['thread'] = threading.Thread(target=capture_loop, daemon=True)
        dev['thread'].start()
    
    def release_device(self, device_index: int, backend: int | None = None):
        key = (device_index, backend)
        if key in self._devices:
            self._devices[key]['refs'] -= 1
            if self._devices[key]['refs'] <= 0:
                self._devices[key]['running'] = False
                self._devices[key]['thread'].join(timeout=1.0)
                self._devices[key]['cap'].release()
                del self._devices[key]
    
    def get_frame(self, device_index: int, backend: int | None = None):
        key = (device_index, backend)
        if key not in self._devices:
            return None
        with self._devices[key]['lock']:
            return self._devices[key]['frame'].copy() if self._devices[key]['frame'] is not None else None


class SharedUSBCaptureSource(VideoSource):
    """USB capture source that shares the device with other routes.
    
    Use this when multiple cameras need to show different crops/regions
    of the same HDMI capture feed (e.g., 2x2 multiview grid).
    """
    
    def __init__(
        self,
        device: int = 0,
        backend: int | None = None,
        route_label: str = "Camera",
    ) -> None:
        super().__init__(route_label)
        self.device = device
        self.backend = backend
        self._pool = _SharedUSBPool()
        self._dev = None
    
    def start(self) -> None:
        if self._running:
            return
        self._dev = self._pool.get_device(self.device, self.backend)
        super().start()
    
    def stop(self) -> None:
        super().stop()
        if self._dev:
            self._pool.release_device(self.device, self.backend)
            self._dev = None
    
    def _capture_loop(self) -> None:
        while self._running:
            frame = self._pool.get_frame(self.device, self.backend)
            if frame is not None:
                self._store_frame(frame)
            time.sleep(0.001)  # Small sleep to prevent busy-waiting


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
