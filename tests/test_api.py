"""Tests for FastAPI endpoints.

Uses TestClient against the FastAPI app.  Because the app stores state in
module-level variables we set them up before creating the client.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from meowcam_bridge.config import BridgeConfig, CameraRoute, MAX_ROUTES
from meowcam_bridge.bridge import BridgeCore


@pytest.fixture
def client(tmp_path: pathlib.Path):
    """Create a TestClient with a fresh BridgeCore already wired in."""
    # Import app late so we can set module state first
    from meowcam_bridge import app as app_module

    cfg = BridgeConfig(
        routes=[
            CameraRoute(enabled=True, label="Main Stage", camera_ip="192.168.1.10"),
            CameraRoute(enabled=False, label="Camera 2", camera_ip="192.168.1.11"),
        ]
    )
    app_module._bridge = BridgeCore(cfg)
    app_module._config_path = tmp_path / "test.json"
    app_module._bridge.config.save(app_module._config_path)

    # Now import the app object itself
    from meowcam_bridge.app import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_returns_config(self, client):
        res = client.get("/api/config")
        assert res.status_code == 200
        data = res.json()
        assert data["bridge_ip"] == "0.0.0.0"
        assert len(data["routes"]) == 2


class TestPutConfig:
    def test_replace_config(self, client):
        new_cfg = {
            "bridge_ip": "127.0.0.1",
            "bridge_ui_port": 9090,
            "controller_bind_ip": "0.0.0.0",
            "routes": [
                {
                    "enabled": True,
                    "label": "Updated",
                    "incoming_port": 52380,
                    "input_profile": "ptzoptics_pt_joy_g4_sony_visca_udp",
                    "output_profile": "sony_brc_h900_brbk_ip10",
                    "camera_ip": "10.0.0.1",
                    "camera_port": 52381,
                    "status": "unknown",
                    "preset_labels": [f"Preset {i}" for i in range(1, 17)],
                }
            ],
        }
        res = client.put("/api/config", json=new_cfg)
        assert res.status_code == 200
        data = res.json()
        assert data["bridge_ip"] == "127.0.0.1"
        assert data["routes"][0]["label"] == "Updated"

    def test_rejects_invalid_config(self, client):
        res = client.put("/api/config", json={"bridge_ui_port": 999999})
        assert res.status_code == 422


class TestExportImportConfig:
    def test_export_matches_current(self, client):
        res = client.post("/api/config/export", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["routes"][0]["label"] == "Main Stage"

    def test_import_replaces(self, client):
        payload = {
            "bridge_ip": "0.0.0.0",
            "bridge_ui_port": 8080,
            "controller_bind_ip": "0.0.0.0",
            "routes": [
                {
                    "enabled": True,
                    "label": "Imported",
                    "incoming_port": 52380,
                    "input_profile": "ptzoptics_pt_joy_g4_sony_visca_udp",
                    "output_profile": "sony_brc_h900_brbk_ip10",
                    "camera_ip": "192.168.1.99",
                    "camera_port": 52381,
                    "status": "unknown",
                    "preset_labels": [f"Preset {i}" for i in range(1, 17)],
                }
            ],
        }
        res = client.post("/api/config/import", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["routes"][0]["label"] == "Imported"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestGetRoutes:
    def test_returns_routes_with_index(self, client):
        res = client.get("/api/routes")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        assert data[0]["index"] == 0
        assert data[0]["label"] == "Main Stage"


class TestPutRoute:
    def test_update_existing(self, client):
        payload = {
            "enabled": True,
            "label": "Renamed",
            "incoming_port": 52380,
            "input_profile": "ptzoptics_pt_joy_g4_sony_visca_udp",
            "output_profile": "sony_brc_h900_brbk_ip10",
            "camera_ip": "192.168.1.10",
            "camera_port": 52381,
            "status": "ok",
            "preset_labels": [f"Preset {i}" for i in range(1, 17)],
        }
        res = client.put("/api/routes/0", json=payload)
        assert res.status_code == 200
        assert res.json()["label"] == "Renamed"

    def test_create_beyond_length(self, client):
        payload = {
            "enabled": True,
            "label": "New",
            "incoming_port": 52390,
            "input_profile": "ptzoptics_pt_joy_g4_sony_visca_udp",
            "output_profile": "sony_brc_h900_brbk_ip10",
            "camera_ip": "192.168.1.50",
            "camera_port": 52381,
            "status": "unknown",
            "preset_labels": [f"Preset {i}" for i in range(1, 17)],
        }
        res = client.put("/api/routes/5", json=payload)
        assert res.status_code == 200
        assert res.json()["label"] == "New"
        # Verify it was appended
        get_res = client.get("/api/routes")
        assert len(get_res.json()) == 6

    def test_rejects_out_of_range(self, client):
        res = client.put(f"/api/routes/{MAX_ROUTES}", json={"enabled": True})
        assert res.status_code == 400


class TestDeleteRoute:
    def test_delete_existing(self, client):
        res = client.delete("/api/routes/1")
        assert res.status_code == 200
        get_res = client.get("/api/routes")
        assert len(get_res.json()) == 1

    def test_delete_out_of_range_ok(self, client):
        res = client.delete("/api/routes/99")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Test camera
# ---------------------------------------------------------------------------

class TestRouteTest:
    def test_version_on_enabled(self, client):
        res = client.post("/api/routes/0/test", json={"type": "version"})
        assert res.status_code == 200
        data = res.json()
        assert data["test_type"] == "version"
        assert data["ok"] is True

    def test_stop_on_disabled(self, client):
        res = client.post("/api/routes/1/test", json={"type": "stop"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False  # disabled route

    def test_unknown_test_type(self, client):
        res = client.post("/api/routes/0/test", json={"type": "nonsense"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False

    def test_route_not_found(self, client):
        res = client.post("/api/routes/99/test", json={"type": "ping"})
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class TestPostCommand:
    def test_pan_left(self, client):
        res = client.post("/api/command", json={"route_index": 0, "command": "pan_left", "args": {"pan_speed": 5}})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["command"] == "pan_left"

    def test_preset_recall(self, client):
        res = client.post("/api/command", json={"route_index": 0, "command": "preset_recall", "args": {"preset": 3}})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True

    def test_disabled_route(self, client):
        res = client.post("/api/command", json={"route_index": 1, "command": "stop"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False

    def test_missing_fields(self, client):
        res = client.post("/api/command", json={"command": "stop"})
        assert res.status_code == 422

    def test_unknown_command(self, client):
        res = client.post("/api/command", json={"route_index": 0, "command": "fly_to_moon"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_get_diagnostics(self, client):
        res = client.get("/api/diagnostics")
        assert res.status_code == 200
        data = res.json()
        assert "routes" in data
        assert "event_log" in data

    def test_reset_diagnostics(self, client):
        # Send a command first to populate diagnostics
        client.post("/api/command", json={"route_index": 0, "command": "stop"})
        res = client.post("/api/diagnostics/reset", json={})
        assert res.status_code == 200
        diag = client.get("/api/diagnostics").json()
        assert diag["command_count"] == 0
        assert diag["event_log"] == []
