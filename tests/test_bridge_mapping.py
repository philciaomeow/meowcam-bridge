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
        with pytest.raises(NotImplementedError):
            import asyncio
            asyncio.run(core.start())
