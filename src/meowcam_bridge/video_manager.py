"""Video source manager for MeowCam Bridge.

Manages per-route video sources, handles start/stop lifecycle alongside
the bridge, and reacts to configuration changes.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import cv2

from .config import CameraVideo
from .video import VideoSource, TestPatternSource, NDISource, USBCaptureSource

if TYPE_CHECKING:
    from .config import BridgeConfig

logger = logging.getLogger(__name__)


class VideoSourceManager:
    """Manages video sources for each camera route.

    Sources are created lazily on first access and cached until the route
    is reconfigured or the manager is shut down.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._sources: dict[int, VideoSource] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start video sources for all enabled routes."""
        for idx, route in self._config.enabled_routes():
            if route.video.enabled:
                self._ensure_source(idx)

    def stop(self) -> None:
        """Stop all managed video sources."""
        for source in self._sources.values():
            try:
                source.stop()
            except Exception as exc:
                logger.warning("Error stopping video source: %s", exc)
        self._sources.clear()

    # ------------------------------------------------------------------
    # Source access
    # ------------------------------------------------------------------

    def get_source(self, route_index: int) -> VideoSource | None:
        """Return the video source for *route_index*, creating it if needed.

        Returns ``None`` if the route has no video configured.
        """
        if route_index < 0 or route_index >= len(self._config.routes):
            return None
        route = self._config.routes[route_index]
        if not route.video.enabled:
            return None
        return self._ensure_source(route_index)

    def _ensure_source(self, route_index: int) -> VideoSource | None:
        """Create or return a cached source for *route_index*."""
        if route_index in self._sources:
            return self._sources[route_index]

        route = self._config.routes[route_index]
        video_cfg = route.video
        source = self._create_source(video_cfg, route.label)
        if source is None:
            return None
        try:
            source.start()
        except Exception as exc:
            logger.error("Failed to start video source for route %s: %s", route.label, exc)
            return None
        self._sources[route_index] = source
        return source

    def _create_source(self, cfg: CameraVideo, label: str) -> VideoSource | None:
        """Instantiate a VideoSource from configuration."""
        match cfg.source_type:
            case "testpattern":
                return TestPatternSource(route_label=label, resolution=cfg.resolution)
            case "ndi":
                try:
                    return NDISource(source_name=cfg.ndi_source_name or None, route_label=label)
                except ImportError:
                    logger.warning("NDI not available; falling back to test pattern for %s", label)
                    return TestPatternSource(route_label=label, resolution=cfg.resolution)
                except Exception as exc:
                    logger.error("NDI source failed for %s: %s", label, exc)
                    return None
            case "usb":
                backend = None
                if sys.platform == "win32":
                    backend = cv2.CAP_DSHOW
                try:
                    return USBCaptureSource(
                        device=cfg.usb_device_index,
                        backend=backend,
                        route_label=label,
                    )
                except Exception as exc:
                    logger.error("USB capture failed for %s: %s", label, exc)
                    return None
            case _:
                return None

    # ------------------------------------------------------------------
    # Config change handling
    # ------------------------------------------------------------------

    def on_config_changed(self) -> None:
        """React to a configuration change.

        Stops sources for routes whose video settings have changed and
        removes them from the cache so they are recreated on next access.
        """
        # Simplest safe approach: stop all sources and clear the cache.
        # The next call to ``get_source`` will recreate with new config.
        self.stop()

    def restart_route(self, route_index: int) -> None:
        """Restart the video source for a single route."""
        source = self._sources.pop(route_index, None)
        if source is not None:
            try:
                source.stop()
            except Exception as exc:
                logger.warning("Error stopping source for route %d: %s", route_index, exc)
        self._ensure_source(route_index)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> BridgeConfig:
        return self._config

    @config.setter
    def config(self, value: BridgeConfig) -> None:
        self._config = value
        self.on_config_changed()
