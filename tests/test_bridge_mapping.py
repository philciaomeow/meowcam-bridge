"""Tests for bridge mapping and sequence state management."""

from __future__ import annotations

import pytest

from meowcam_bridge.bridge import BridgeCore
from meowcam_bridge.config import BridgeConfig, CameraRoute


class TestBridgeCore:
    def test_route_state_created(self):
        cfg = BridgeConfig(routes=[CameraRoute()])
        core = BridgeCore(cfg)
        s1 = core.route_state(0)
        s2 = core.route_state(0)
        assert s1 is s2
        assert isinstance(s1, dict)

    def test_route_state_isolated(self):
        cfg = BridgeConfig(routes=[CameraRoute(), CameraRoute()])
        core = BridgeCore(cfg)
        s0 = core.route_state(0)
        s1 = core.route_state(1)
        s0["x"] = 1
        assert "x" not in s1

    def test_start_stop_not_implemented(self):
        cfg = BridgeConfig()
        core = BridgeCore(cfg)
        pytest.skip("BridgeCore.start/stop now implemented")


class TestBridgeCoreCommands:
    """Test command dispatch for version inquiry, preset recall/save, pan/tilt stop."""

    @pytest.mark.asyncio
    async def test_version_inquiry_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.test_route(0, "version")
        assert result.ok is True
        assert "version inquiry" in result.detail.lower() or "built" in result.result

    @pytest.mark.asyncio
    async def test_stop_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.test_route(0, "stop")
        assert result.ok is True
        assert "stop" in result.detail.lower() or "built" in result.result

    @pytest.mark.asyncio
    async def test_preset_recall_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "preset_recall", {"preset": 5})
        assert result.ok is True
        assert "3f 02 05" in result.detail.lower() or "3f0205" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_preset_save_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "preset_save", {"preset": 3})
        assert result.ok is True
        assert "3f 01 03" in result.detail.lower() or "3f0103" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_pan_tilt_stop_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "stop", {})
        assert result.ok is True
        assert "01 06 01 01 01 03 03 ff" in result.detail.lower() or "01060101010303ff" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_disabled_route_rejected(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=False, label="Off")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "stop", {})
        assert result.ok is False
        assert "disabled" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_out_of_range_route_rejected(self):
        cfg = BridgeConfig()
        core = BridgeCore(cfg)
        result = await core.send_command(0, "stop", {})
        assert result.ok is False
        assert "out of range" in result.detail.lower()


class TestBridgeCoreOSDCommands:
    """Test OSD menu command payloads for BRC-H900 compatibility.

    The Sony BRC-H900 VISCA OSD commands are:
      menu_open  = 01 06 06 02 FF
      menu_close = 01 06 06 03 FF
      menu_enter = 01 06 06 05 FF
      menu_back  = 01 06 06 04 FF
    """

    @pytest.mark.asyncio
    async def test_menu_open_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "menu_open", {})
        assert result.ok is True
        assert "01060602ff" in result.detail.replace(" ", "").lower()

    @pytest.mark.asyncio
    async def test_menu_close_payload(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "menu_close", {})
        assert result.ok is True
        assert "01060603ff" in result.detail.replace(" ", "").lower()

    @pytest.mark.asyncio
    async def test_menu_enter_payload(self):
        """menu_enter must be 01 06 06 05 FF, NOT the old 01 7E 01 02 00 01 FF."""
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "menu_enter", {})
        assert result.ok is True
        detail = result.detail.replace(" ", "").lower()
        assert "01060605ff" in detail
        # Explicitly verify old wrong payload is NOT present
        assert "017e0102000 1ff" not in detail
        assert "017e01020001ff" not in detail

    @pytest.mark.asyncio
    async def test_menu_back_payload(self):
        """menu_back must be 01 06 06 04 FF, NOT the old 01 7E 01 02 00 02 FF."""
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Test")])
        core = BridgeCore(cfg)
        result = await core.send_command(0, "menu_back", {})
        assert result.ok is True
        detail = result.detail.replace(" ", "").lower()
        assert "01060604ff" in detail
        assert "017e01020002ff" not in detail


class TestBridgeCoreSequenceMapping:
    """Test sequence mapping between controller and camera sides."""

    def test_route_state_sequence_tracking(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="Seq")])
        core = BridgeCore(cfg)
        state = core.route_state(0)
        state["sony_seq"] = 10
        assert core.route_state(0)["sony_seq"] == 10

    def test_pending_replies_isolated_by_route(self):
        cfg = BridgeConfig(routes=[CameraRoute(enabled=True, label="A"), CameraRoute(enabled=True, label="B")])
        core = BridgeCore(cfg)
        core._pending_replies[0] = {1: (42, ("192.168.1.50", 52380), "visca_ip")}
        core._pending_replies[1] = {2: (99, ("192.168.1.51", 52380), "visca_ip")}
        assert core._pending_replies[0][1][0] == 42
        assert core._pending_replies[1][2][0] == 99
