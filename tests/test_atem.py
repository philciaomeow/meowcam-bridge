"""Tests for ATEM integration — config model, ATEMManager with mocked switcher, and API endpoints."""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from meowcam_bridge.config import AtemConfig, BridgeConfig, CameraRoute, MAX_ROUTES
from meowcam_bridge.bridge import BridgeCore
from meowcam_bridge.atem import (
    ATEMManager,
    ATEMConnectionError,
    GRID_2X2_POSITIONS,
    NUM_BOXES,
    NUM_INPUTS,
)


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------

class TestAtemConfig:
    def test_defaults(self):
        cfg = AtemConfig()
        assert cfg.enabled is False
        assert cfg.atem_ip == "192.168.1.240"
        assert cfg.supersource_aux_output == 1
        assert len(cfg.input_mapping) == MAX_ROUTES
        assert cfg.input_mapping == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_aux_output_range(self):
        with pytest.raises(ValueError):
            AtemConfig(supersource_aux_output=0)
        with pytest.raises(ValueError):
            AtemConfig(supersource_aux_output=7)

    def test_input_mapping_wrong_length(self):
        with pytest.raises(ValueError):
            AtemConfig(input_mapping=[1, 2, 3])

    def test_input_mapping_out_of_range(self):
        with pytest.raises(ValueError):
            AtemConfig(input_mapping=[0, 2, 3, 4, 5, 6, 7, 8])
        with pytest.raises(ValueError):
            AtemConfig(input_mapping=[1, 2, 3, 4, 5, 6, 7, 21])

    def test_valid_custom_config(self):
        cfg = AtemConfig(
            enabled=True,
            atem_ip="10.0.0.50",
            supersource_aux_output=3,
            input_mapping=[5, 6, 7, 8, 1, 2, 3, 4],
        )
        assert cfg.enabled is True
        assert cfg.atem_ip == "10.0.0.50"
        assert cfg.supersource_aux_output == 3
        assert cfg.input_mapping[0] == 5

    def test_roundtrip_json(self, tmp_path: pathlib.Path):
        cfg = BridgeConfig(
            routes=[CameraRoute(enabled=True, label="Main")],
            atem=AtemConfig(enabled=True, atem_ip="10.0.0.99", supersource_aux_output=2),
        )
        path = tmp_path / "config.json"
        cfg.save(path)
        loaded = BridgeConfig.load(path)
        assert loaded.atem.enabled is True
        assert loaded.atem.atem_ip == "10.0.0.99"
        assert loaded.atem.supersource_aux_output == 2

    def test_bridge_config_has_atem(self):
        cfg = BridgeConfig()
        assert hasattr(cfg, "atem")
        assert isinstance(cfg.atem, AtemConfig)
        assert cfg.atem.enabled is False

    def test_bridge_config_with_atem_in_json(self):
        payload = {
            "bridge_ip": "0.0.0.0",
            "bridge_ui_port": 8080,
            "controller_bind_ip": "0.0.0.0",
            "routes": [],
            "atem": {
                "enabled": True,
                "atem_ip": "192.168.1.100",
                "supersource_aux_output": 4,
                "input_mapping": [10, 11, 12, 13, 14, 15, 16, 17],
            },
        }
        cfg = BridgeConfig.model_validate(payload)
        assert cfg.atem.enabled is True
        assert cfg.atem.atem_ip == "192.168.1.100"
        assert cfg.atem.input_mapping[0] == 10


# ---------------------------------------------------------------------------
# ATEMManager tests (mocked switcher)
# ---------------------------------------------------------------------------

class _MockPosition:
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y


class _MockBoxParams:
    def __init__(self, enabled=False, input_source=None, size=0.0, x=0.0, y=0.0):
        self.enabled = enabled
        self.inputSource = input_source
        self.size = size
        self.position = _MockPosition(x, y)


class _MockSuperSource:
    def __init__(self):
        self.boxParameters = [_MockBoxParams() for _ in range(4)]


class _MockProgramInput:
    def __init__(self, source=0):
        self.videoSource = MagicMock()
        self.videoSource.value = source


class _MockAuxSource:
    def __init__(self, source=0):
        self.input = MagicMock()
        self.input.value = source


class _MockInputProperties:
    def __init__(self, long_name="", short_name=""):
        self.longName = long_name
        self.shortName = short_name


class _MockSwitcher:
    """Minimal mock of PyATEMMax.ATEMMax for testing."""

    def __init__(self):
        self.superSource = _MockSuperSource()
        self.programInput = [_MockProgramInput(1)]
        self.previewInput = [_MockProgramInput(2)]
        self.auxSource = [_MockAuxSource(6000)]
        self.inputProperties = {i: _MockInputProperties(f"Input {i}", f"IN{i}") for i in range(1, 21)}
        self._sent_commands = []

    def setSuperSourceForeground(self, flag):
        self._sent_commands.append(("setSuperSourceForeground", flag))

    def _box_idx(self, box):
        """Convert ATEMBoxes constant or int to int index."""
        return box.value if hasattr(box, "value") else box

    def setSuperSourceBoxParametersEnabled(self, box, enabled):
        idx = self._box_idx(box)
        self._sent_commands.append(("setEnabled", idx, enabled))
        self.superSource.boxParameters[idx].enabled = enabled

    def setSuperSourceBoxParametersInputSource(self, box, source):
        idx = self._box_idx(box)
        self._sent_commands.append(("setInputSource", idx, source))

    def setSuperSourceBoxParametersPositionX(self, box, x):
        idx = self._box_idx(box)
        self._sent_commands.append(("setPositionX", idx, x))
        self.superSource.boxParameters[idx].position.x = x

    def setSuperSourceBoxParametersPositionY(self, box, y):
        idx = self._box_idx(box)
        self._sent_commands.append(("setPositionY", idx, y))
        self.superSource.boxParameters[idx].position.y = y

    def setSuperSourceBoxParametersSize(self, box, size):
        idx = self._box_idx(box)
        self._sent_commands.append(("setSize", idx, size))
        self.superSource.boxParameters[idx].size = size

    def setSuperSourceBoxParametersCropped(self, box, flag):
        idx = self._box_idx(box)
        self._sent_commands.append(("setCropped", idx, flag))

    def setAuxSourceInput(self, channel, source):
        self._sent_commands.append(("setAuxSourceInput", channel, source))

    def connect(self, ip):
        pass

    def waitForConnection(self):
        pass

    def disconnect(self):
        pass


def _make_connected_manager(config=None):
    """Create an ATEMManager with a mock switcher that's 'connected'."""
    if config is None:
        config = AtemConfig(enabled=True, atem_ip="192.168.1.240")
    manager = ATEMManager(config)
    mock_sw = _MockSwitcher()
    manager._switcher = mock_sw
    manager._connected = True
    return manager, mock_sw


class TestATEMManagerConnection:
    def test_not_connected_raises(self):
        mgr = ATEMManager(AtemConfig())
        with pytest.raises(ATEMConnectionError):
            mgr._ensure_connected()

    def test_connected_property(self):
        mgr = ATEMManager(AtemConfig())
        assert mgr.connected is False
        mgr._switcher = _MockSwitcher()
        mgr._connected = True
        assert mgr.connected is True

    def test_connect_with_mock(self):
        mgr = ATEMManager(AtemConfig(enabled=True, atem_ip="10.0.0.1"))
        mock_sw = _MockSwitcher()
        with patch("PyATEMMax.ATEMMax", return_value=mock_sw):
            mgr.connect()
        assert mgr.connected is True
        assert mgr._switcher is mock_sw

    def test_disconnect(self):
        mgr, _ = _make_connected_manager()
        mgr.disconnect()
        assert mgr.connected is False
        assert mgr._switcher is None

    def test_status(self):
        mgr, _ = _make_connected_manager()
        status = mgr.status()
        assert status["connected"] is True
        assert status["atem_ip"] == "192.168.1.240"
        assert status["enabled"] is True


class TestATEMManagerSuperSource:
    def test_configure_2x2_default_mapping(self):
        mgr, sw = _make_connected_manager()
        result = mgr.configure_supersource_2x2()
        assert "boxes" in result
        assert len(result["boxes"]) == NUM_BOXES
        for i, box in enumerate(result["boxes"]):
            assert box["enabled"] is True
            assert box["input_source"] == mgr._config.input_mapping[i]
            assert box["position_x"] == GRID_2X2_POSITIONS[i]["x"]
            assert box["position_y"] == GRID_2X2_POSITIONS[i]["y"]
            assert box["size"] == 0.5
        # Verify foreground was disabled
        assert ("setSuperSourceForeground", False) in sw._sent_commands

    def test_configure_2x2_custom_mapping(self):
        mgr, sw = _make_connected_manager()
        result = mgr.configure_supersource_2x2(input_mapping=[10, 11, 12, 13])
        assert result["boxes"][0]["input_source"] == 10
        assert result["boxes"][3]["input_source"] == 13

    def test_configure_2x2_too_few_inputs(self):
        mgr, _ = _make_connected_manager()
        with pytest.raises(ValueError, match="Need 4"):
            mgr.configure_supersource_2x2(input_mapping=[1, 2])

    def test_route_supersource_to_aux(self):
        mgr, sw = _make_connected_manager()
        result = mgr.route_supersource_to_aux(3)
        assert result["aux_output"] == 3
        assert result["source"] == "superSource"
        assert any(cmd[0] == "setAuxSourceInput" for cmd in sw._sent_commands)

    def test_route_supersource_to_aux_default(self):
        mgr, _ = _make_connected_manager(AtemConfig(enabled=True, supersource_aux_output=2))
        result = mgr.route_supersource_to_aux()
        assert result["aux_output"] == 2

    def test_route_supersource_to_aux_invalid(self):
        mgr, _ = _make_connected_manager()
        with pytest.raises(ValueError):
            mgr.route_supersource_to_aux(7)

    def test_get_supersource_state(self):
        mgr, _ = _make_connected_manager()
        # Set up box state
        for i in range(NUM_BOXES):
            bp = mgr._switcher.superSource.boxParameters[i]
            bp.enabled = True
            bp.inputSource = MagicMock()
            bp.inputSource.value = i + 1
            bp.position.x = GRID_2X2_POSITIONS[i]["x"]
            bp.position.y = GRID_2X2_POSITIONS[i]["y"]
            bp.size = 0.5
        state = mgr.get_supersource_state()
        assert len(state["boxes"]) == NUM_BOXES
        assert state["boxes"][0]["enabled"] is True
        assert state["boxes"][0]["input_source"] == 1
        assert state["aux_output"] == mgr._config.supersource_aux_output

    def test_toggle_box_off(self):
        mgr, sw = _make_connected_manager()
        # Set box 0 as enabled
        sw.superSource.boxParameters[0].enabled = True
        result = mgr.toggle_box(0, enabled=False)
        assert result["box"] == 0
        assert result["enabled"] is False

    def test_toggle_box_on(self):
        mgr, sw = _make_connected_manager()
        result = mgr.toggle_box(1, enabled=True)
        assert result["box"] == 1
        assert result["enabled"] is True

    def test_toggle_box_auto_toggle(self):
        mgr, sw = _make_connected_manager()
        sw.superSource.boxParameters[2].enabled = False
        result = mgr.toggle_box(2)  # should toggle to True
        assert result["enabled"] is True

    def test_toggle_box_invalid_index(self):
        mgr, _ = _make_connected_manager()
        with pytest.raises(ValueError):
            mgr.toggle_box(5)

    def test_toggle_box_negative_index(self):
        mgr, _ = _make_connected_manager()
        with pytest.raises(ValueError):
            mgr.toggle_box(-1)


class TestATEMManagerTally:
    def test_get_tally(self):
        mgr, _ = _make_connected_manager()
        mgr._switcher.programInput[0].videoSource.value = 5
        mgr._switcher.previewInput[0].videoSource.value = 3
        result = mgr.get_tally(0)
        assert result["me_index"] == 0
        assert result["pgm_source"] == 5
        assert result["pvw_source"] == 3

    def test_get_tally_none_source(self):
        mgr, _ = _make_connected_manager()
        mgr._switcher.programInput[0].videoSource.value = None
        mgr._switcher.previewInput[0].videoSource.value = None
        result = mgr.get_tally(0)
        assert result["pgm_source"] is None
        assert result["pvw_source"] is None


class TestATEMManagerInputs:
    def test_get_inputs(self):
        mgr, _ = _make_connected_manager()
        inputs = mgr.get_inputs()
        assert len(inputs) == NUM_INPUTS
        assert inputs[0]["source"] == 1
        assert inputs[0]["long_name"] == "Input 1"
        assert inputs[0]["short_name"] == "IN1"
        assert inputs[19]["source"] == 20

    def test_get_inputs_missing_input(self):
        mgr, _ = _make_connected_manager()
        # Remove input 15 from mock
        del mgr._switcher.inputProperties[15]
        inputs = mgr.get_inputs()
        assert len(inputs) == NUM_INPUTS
        assert inputs[14]["source"] == 15
        assert inputs[14]["long_name"] == ""


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path: pathlib.Path):
    """Create a TestClient with a fresh BridgeCore already wired in."""
    from meowcam_bridge import app as app_module

    cfg = BridgeConfig(
        routes=[
            CameraRoute(enabled=True, label="Main Stage", camera_ip="192.168.1.10"),
            CameraRoute(enabled=False, label="Camera 2", camera_ip="192.168.1.11"),
        ],
        atem=AtemConfig(enabled=True, atem_ip="192.168.1.240"),
    )
    app_module._bridge = BridgeCore(cfg)
    app_module._config_path = tmp_path / "test.json"
    app_module._bridge.config.save(app_module._config_path)
    app_module._atem_manager = None

    from meowcam_bridge.app import app
    return TestClient(app)


@pytest.fixture
def atem_client(client):
    """Create a TestClient with a mocked ATEM manager connected."""
    from meowcam_bridge import app as app_module

    mgr, mock_sw = _make_connected_manager()
    app_module._atem_manager = mgr
    yield client, mock_sw
    app_module._atem_manager = None


class TestATEMStatusEndpoint:
    def test_status_not_connected(self, client):
        res = client.get("/api/atem/status")
        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is False
        assert data["enabled"] is True

    def test_status_connected(self, atem_client):
        client, _ = atem_client
        res = client.get("/api/atem/status")
        assert res.status_code == 200
        assert res.json()["connected"] is True


class TestATEMSuperSourceEndpoint:
    def test_get_supersource(self, atem_client):
        client, _ = atem_client
        res = client.get("/api/atem/supersource")
        assert res.status_code == 200
        data = res.json()
        assert "boxes" in data
        assert len(data["boxes"]) == NUM_BOXES
        assert "aux_output" in data

    def test_put_supersource_default(self, atem_client):
        client, sw = atem_client
        res = client.put("/api/atem/supersource", json={})
        assert res.status_code == 200
        data = res.json()
        assert len(data["boxes"]) == NUM_BOXES
        assert data["boxes"][0]["enabled"] is True

    def test_put_supersource_custom_mapping(self, atem_client):
        client, _ = atem_client
        res = client.put("/api/atem/supersource", json={"input_mapping": [10, 11, 12, 13]})
        assert res.status_code == 200
        data = res.json()
        assert data["boxes"][0]["input_source"] == 10
        assert data["boxes"][3]["input_source"] == 13

    def test_put_supersource_with_aux(self, atem_client):
        client, _ = atem_client
        res = client.put("/api/atem/supersource", json={"aux_output": 3})
        assert res.status_code == 200
        assert res.json()["aux_output"] == 3

    def test_get_supersource_not_connected(self, client):
        res = client.get("/api/atem/supersource")
        assert res.status_code == 503


class TestATEMToggleEndpoint:
    def test_toggle_off(self, atem_client):
        client, sw = atem_client
        sw.superSource.boxParameters[0].enabled = True
        res = client.post("/api/atem/supersource/box/0/toggle", json={"enabled": False})
        assert res.status_code == 200
        assert res.json()["enabled"] is False

    def test_toggle_on(self, atem_client):
        client, _ = atem_client
        res = client.post("/api/atem/supersource/box/1/toggle", json={"enabled": True})
        assert res.status_code == 200
        assert res.json()["enabled"] is True

    def test_toggle_auto(self, atem_client):
        client, sw = atem_client
        sw.superSource.boxParameters[0].enabled = False
        res = client.post("/api/atem/supersource/box/0/toggle", json={})
        assert res.status_code == 200
        assert res.json()["enabled"] is True

    def test_toggle_invalid_box(self, atem_client):
        client, _ = atem_client
        res = client.post("/api/atem/supersource/box/5/toggle", json={})
        assert res.status_code == 422


class TestATEMTallyEndpoint:
    def test_get_tally(self, atem_client):
        client, _ = atem_client
        res = client.get("/api/atem/tally")
        assert res.status_code == 200
        data = res.json()
        assert "pgm_source" in data
        assert "pvw_source" in data
        assert "me_index" in data

    def test_get_tally_not_connected(self, client):
        res = client.get("/api/atem/tally")
        assert res.status_code == 503


class TestATEMInputsEndpoint:
    def test_get_inputs(self, atem_client):
        client, _ = atem_client
        res = client.get("/api/atem/inputs")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == NUM_INPUTS
        assert data[0]["source"] == 1
        assert "long_name" in data[0]
        assert "short_name" in data[0]

    def test_get_inputs_not_connected(self, client):
        res = client.get("/api/atem/inputs")
        assert res.status_code == 503


class TestATEMConfigEndpoint:
    def test_get_atem_config(self, client):
        res = client.get("/api/atem/config")
        assert res.status_code == 200
        data = res.json()
        assert data["atem_ip"] == "192.168.1.240"
        assert data["enabled"] is True

    def test_put_atem_config(self, client):
        res = client.put("/api/atem/config", json={
            "enabled": True,
            "atem_ip": "10.0.0.99",
            "supersource_aux_output": 3,
            "input_mapping": [5, 6, 7, 8, 1, 2, 3, 4],
        })
        assert res.status_code == 200
        data = res.json()
        assert data["atem_ip"] == "10.0.0.99"
        assert data["supersource_aux_output"] == 3

    def test_put_atem_config_invalid(self, client):
        res = client.put("/api/atem/config", json={"supersource_aux_output": 99})
        assert res.status_code == 422


class TestATEMConnectEndpoint:
    def test_disconnect(self, atem_client):
        client, _ = atem_client
        res = client.post("/api/atem/disconnect")
        assert res.status_code == 200
        assert res.json()["connected"] is False