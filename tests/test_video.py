"""Tests for video capture module and endpoints.

DO NOT require NDI SDK or camera hardware for tests.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from meowcam_bridge.config import BridgeConfig, CameraRoute, CameraVideo
from meowcam_bridge.bridge import BridgeCore
from meowcam_bridge.video import TestPatternSource, snapshot_response
from meowcam_bridge.video_manager import VideoSourceManager


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

class TestCameraVideo:
    def test_defaults(self):
        v = CameraVideo()
        assert v.enabled is False
        assert v.source_type == "none"
        assert v.resolution == "640x360"
        assert v.frame_rate == 8
        assert v.jpeg_quality == 60

    def test_resolution_validation(self):
        v = CameraVideo(resolution="1280x720")
        assert v.resolution == "1280x720"

    def test_resolution_invalid(self):
        with pytest.raises(ValueError):
            CameraVideo(resolution="bad")
        with pytest.raises(ValueError):
            CameraVideo(resolution="1280x")

    def test_source_type_literal(self):
        v = CameraVideo(source_type="testpattern")
        assert v.source_type == "testpattern"
        with pytest.raises(ValueError):
            CameraVideo(source_type="invalid")


class TestCameraRouteWithVideo:
    def test_video_defaults(self):
        r = CameraRoute()
        assert r.video.enabled is False
        assert r.video.source_type == "none"

    def test_video_embedded(self):
        r = CameraRoute(
            video=CameraVideo(
                enabled=True,
                source_type="testpattern",
                resolution="1280x720",
                frame_rate=15,
                jpeg_quality=80,
            )
        )
        assert r.video.enabled is True
        assert r.video.frame_rate == 15


# ---------------------------------------------------------------------------
# Test pattern source
# ---------------------------------------------------------------------------

class TestTestPatternSource:
    def test_produces_valid_jpeg(self):
        src = TestPatternSource(route_label="Test Cam", resolution="320x240")
        src.start()
        import time
        time.sleep(0.1)  # let thread render at least one frame
        jpeg = src.get_jpeg()
        src.stop()
        assert jpeg is not None
        assert len(jpeg) > 0
        assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker

    def test_resizing(self):
        src = TestPatternSource(route_label="Test Cam", resolution="640x360")
        src.start()
        import time
        time.sleep(0.1)
        jpeg = src.get_jpeg(width=320)
        src.stop()
        assert jpeg is not None

    def test_snapshot_response(self):
        src = TestPatternSource(route_label="Test Cam", resolution="320x240")
        src.start()
        import time
        time.sleep(0.1)
        jpeg, media_type = snapshot_response(src)
        src.stop()
        assert media_type == "image/jpeg"
        assert jpeg[:2] == b"\xff\xd8"


# ---------------------------------------------------------------------------
# VideoSourceManager
# ---------------------------------------------------------------------------

class TestVideoSourceManager:
    def test_get_source_returns_none_for_disabled(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, video=CameraVideo(enabled=False))])
        mgr = VideoSourceManager(cfg)
        assert mgr.get_source(0) is None

    def test_get_source_creates_testpattern(self):
        cfg = BridgeConfig(
            routes=[
                CameraRoute(
                    enabled=True,
                    label="Cam 1",
                    video=CameraVideo(enabled=True, source_type="testpattern"),
                )
            ]
        )
        mgr = VideoSourceManager(cfg)
        source = mgr.get_source(0)
        assert source is not None
        import time
        time.sleep(0.15)
        jpeg = source.get_jpeg()
        mgr.stop()
        assert jpeg is not None
        assert jpeg[:2] == b"\xff\xd8"

    def test_on_config_changed_clears_sources(self):
        cfg = BridgeConfig(
            routes=[
                CameraRoute(
                    enabled=True,
                    video=CameraVideo(enabled=True, source_type="testpattern"),
                )
            ]
        )
        mgr = VideoSourceManager(cfg)
        mgr.start()
        assert mgr.get_source(0) is not None
        mgr.on_config_changed()
        assert len(mgr._sources) == 0


# ---------------------------------------------------------------------------
# FastAPI endpoints (with mock video manager)
# ---------------------------------------------------------------------------

@pytest.fixture
def client_with_video(tmp_path: pathlib.Path):
    """Create a TestClient with a video-enabled route."""
    from meowcam_bridge import app as app_module

    cfg = BridgeConfig(
        routes=[
            CameraRoute(
                enabled=True,
                label="Video Cam",
                camera_ip="192.168.1.10",
                video=CameraVideo(enabled=True, source_type="testpattern", frame_rate=10),
            ),
            CameraRoute(
                enabled=True,
                label="No Video",
                camera_ip="192.168.1.11",
                video=CameraVideo(enabled=False),
            ),
        ]
    )
    app_module._bridge = BridgeCore(cfg)
    app_module._config_path = tmp_path / "test.json"
    app_module._bridge.config.save(app_module._config_path)
    app_module._video_manager = VideoSourceManager(cfg)
    app_module._video_manager.start()

    from meowcam_bridge.app import app

    client = TestClient(app)
    yield client
    app_module._video_manager.stop()


class TestVideoSnapshot:
    def test_snapshot_returns_jpeg(self, client_with_video):
        res = client_with_video.get("/api/video/snapshot/0")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.content[:2] == b"\xff\xd8"

    def test_snapshot_disabled_route(self, client_with_video):
        res = client_with_video.get("/api/video/snapshot/1")
        assert res.status_code == 404

    def test_snapshot_out_of_range(self, client_with_video):
        res = client_with_video.get("/api/video/snapshot/99")
        assert res.status_code == 404


class TestVideoFeed:
    def test_feed_disabled_route(self, client_with_video):
        res = client_with_video.get("/api/video/feed/1")
        assert res.status_code == 404

    def test_feed_out_of_range(self, client_with_video):
        res = client_with_video.get("/api/video/feed/99")
        assert res.status_code == 404
