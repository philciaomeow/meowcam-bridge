"""Tests for bridge configuration models."""

from __future__ import annotations

import json
import pathlib

import pytest

from meowcam_bridge.config import BridgeConfig, CameraRoute, MAX_ROUTES


class TestCameraRoute:
    def test_defaults(self):
        r = CameraRoute()
        assert r.enabled is False
        assert r.label == "Camera"
        assert r.incoming_port == 52380
        assert r.input_profile == "ptzoptics_pt_joy_g4_sony_visca_udp"
        assert r.output_profile == "sony_brc_h900_brbk_ip10"
        assert r.camera_ip == "192.168.1.100"
        assert r.camera_port == 52381
        assert len(r.preset_labels) == 16

    def test_preset_labels_truncated(self):
        r = CameraRoute(preset_labels=["A"] * 20)
        assert len(r.preset_labels) == 16

    def test_port_validation(self):
        with pytest.raises(ValueError):
            CameraRoute(incoming_port=0)
        with pytest.raises(ValueError):
            CameraRoute(incoming_port=70000)


class TestBridgeConfig:
    def test_defaults(self):
        cfg = BridgeConfig()
        assert cfg.bridge_ip == "0.0.0.0"
        assert cfg.bridge_ui_port == 8080
        assert cfg.routes == []

    def test_max_routes(self):
        routes = [CameraRoute()] * (MAX_ROUTES + 1)
        with pytest.raises(ValueError):
            BridgeConfig(routes=routes)

    def test_enabled_routes(self):
        routes = [
            CameraRoute(enabled=True, label="A"),
            CameraRoute(enabled=False, label="B"),
            CameraRoute(enabled=True, label="C"),
        ]
        cfg = BridgeConfig(routes=routes)
        enabled = cfg.enabled_routes()
        assert len(enabled) == 2
        assert enabled[0][1].label == "A"
        assert enabled[1][1].label == "C"

    def test_roundtrip_json(self, tmp_path: pathlib.Path):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Main")])
        path = tmp_path / "config.json"
        cfg.save(path)
        loaded = BridgeConfig.load(path)
        assert loaded.routes[0].label == "Main"
        assert loaded.routes[0].enabled is True

    def test_example_config_loads(self):
        example = pathlib.Path(__file__).parent.parent / "examples" / "config.example.json"
        if example.exists():
            cfg = BridgeConfig.load(example)
            assert len(cfg.routes) <= MAX_ROUTES
            assert cfg.routes[0].label == "Main Stage"
