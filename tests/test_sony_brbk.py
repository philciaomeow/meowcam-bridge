"""Tests for Sony BRC-H900 / BRBK-IP10 output profile."""

from __future__ import annotations

from meowcam_bridge.protocols.output_sony_brbk import SonyBRCH900BRBKIP10
from meowcam_bridge.protocols.visca import VISCA_COMMAND_TYPE, parse_visca_ip_packet


class TestSonyBRCH900BRBKIP10:
    def test_source_port(self):
        p = SonyBRCH900BRBKIP10()
        assert p.source_port() == 52381

    def test_supports_pan_tilt(self):
        p = SonyBRCH900BRBKIP10()
        assert p.supports("pan_tilt") is True
        assert p.supports("zoom") is True
        assert p.supports("preset_recall") is True
        assert p.supports("nonexistent") is False

    def test_encode_forces_address(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {}
        # Controller might send address 0x88 (camera 8); profile forces 0x81
        payload = b"\x88\x01\x06\x01\x03\x03\xFF"
        cmd = {"payload": payload, "payload_type": VISCA_COMMAND_TYPE}
        packet = p.encode(cmd, state)
        assert packet is not None
        parsed = parse_visca_ip_packet(packet)
        assert parsed is not None
        _, _, seq, out_payload = parsed
        assert seq == 1
        assert out_payload[0] == 0x81
        assert out_payload[1:] == payload[1:]

    def test_encode_prepends_address_for_bridge_generated_payload(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {}
        # Bridge-generated UI/API commands omit the address byte.
        payload = b"\x01\x06\x06\x05\xFF"
        cmd = {"payload": payload, "payload_type": VISCA_COMMAND_TYPE}
        packet = p.encode(cmd, state)
        assert packet is not None
        parsed = parse_visca_ip_packet(packet)
        assert parsed is not None
        _, _, _, out_payload = parsed
        assert out_payload == b"\x81\x01\x06\x06\x05\xFF"

    def test_sequence_increments(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {}
        cmd = {"payload": b"\x81\x01\x06\x01\x03\x03\xFF", "payload_type": VISCA_COMMAND_TYPE}
        p.encode(cmd, state)
        p.encode(cmd, state)
        packet = p.encode(cmd, state)
        parsed = parse_visca_ip_packet(packet)
        assert parsed is not None
        assert parsed[2] == 3

    def test_decode_reply(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {}
        from meowcam_bridge.protocols.visca import build_visca_ip_packet
        reply = build_visca_ip_packet(0x0111, 7, b"\x81\x50\xFF")
        decoded = p.decode_reply(reply, state)
        assert decoded is not None
        assert decoded["seq"] == 7
        assert decoded["payload"] == b"\x81\x50\xFF"


class TestSonyBRCH900BRBKIP10SequenceRewrite:
    """Test clean camera-side sequence rewrite and reply handling."""

    def test_sequence_wraps_at_32bit(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {"sony_seq": 0xFFFFFFFF}
        cmd = {"payload": b"\x81\x01\x06\x01\x03\x03\xFF", "payload_type": 0x0200}
        packet = p.encode(cmd, state)
        parsed = parse_visca_ip_packet(packet)
        assert parsed is not None
        assert parsed[2] == 1  # wraps to 1 (0 is reserved for control)

    def test_decode_reply_extracts_seq(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {}
        from meowcam_bridge.protocols.visca import build_visca_ip_packet
        reply = build_visca_ip_packet(0x0111, 12345, b"\x81\x50\xFF")
        decoded = p.decode_reply(reply, state)
        assert decoded is not None
        assert decoded["seq"] == 12345
        assert decoded["payload"] == b"\x81\x50\xFF"

    def test_encode_preserves_payload_type(self):
        p = SonyBRCH900BRBKIP10()
        state: dict = {}
        from meowcam_bridge.protocols.visca import VISCA_INQUIRY_TYPE
        cmd = {"payload": b"\x81\x09\x00\x02\xFF", "payload_type": VISCA_INQUIRY_TYPE}
        packet = p.encode(cmd, state)
        parsed = parse_visca_ip_packet(packet)
        assert parsed is not None
        assert parsed[0] == VISCA_INQUIRY_TYPE


class TestSonyBRCH900BRBKIP10RouteSelection:
    """Test per-route state isolation for sequence counters."""

    def test_multiple_routes_independent_sequences(self):
        p = SonyBRCH900BRBKIP10()
        state_a: dict = {}
        state_b: dict = {}
        cmd = {"payload": b"\x81\x01\x06\x01\x03\x03\xFF", "payload_type": 0x0200}
        p.encode(cmd, state_a)
        p.encode(cmd, state_a)
        p.encode(cmd, state_b)
        assert state_a["sony_seq"] == 2
        assert state_b["sony_seq"] == 1

    def test_source_port_fixed(self):
        p = SonyBRCH900BRBKIP10()
        assert p.source_port() == 52381
        assert p.CAMERA_REPLY_PORT == 65000


class TestSonyBRCH900OSDPayloads:
    """Test OSD payload overrides on the Sony BRC-H900 output profile."""

    def test_osd_payloads_defined(self):
        p = SonyBRCH900BRBKIP10()
        assert "menu_open" in p.OSD_PAYLOADS
        assert "menu_close" in p.OSD_PAYLOADS
        assert "menu_enter" in p.OSD_PAYLOADS
        assert "menu_back" in p.OSD_PAYLOADS

    def test_menu_enter_is_sony_osd_select(self):
        """menu_enter must be 01 06 06 05 FF — the Sony OSD Enter/Select command."""
        p = SonyBRCH900BRBKIP10()
        assert p.OSD_PAYLOADS["menu_enter"] == bytes([0x01, 0x06, 0x06, 0x05, 0xFF])

    def test_menu_back_is_sony_osd_return(self):
        """menu_back must be 01 06 06 04 FF — the Sony OSD Back/Return command."""
        p = SonyBRCH900BRBKIP10()
        assert p.OSD_PAYLOADS["menu_back"] == bytes([0x01, 0x06, 0x06, 0x04, 0xFF])

    def test_menu_open_is_sony_osd_on(self):
        """menu_open must be 01 06 06 02 FF — the Sony OSD On command."""
        p = SonyBRCH900BRBKIP10()
        assert p.OSD_PAYLOADS["menu_open"] == bytes([0x01, 0x06, 0x06, 0x02, 0xFF])

    def test_menu_close_is_sony_osd_off(self):
        """menu_close must be 01 06 06 03 FF — the Sony OSD Off command."""
        p = SonyBRCH900BRBKIP10()
        assert p.OSD_PAYLOADS["menu_close"] == bytes([0x01, 0x06, 0x06, 0x03, 0xFF])

    def test_osd_payloads_not_legacy_7e(self):
        """Ensure no OSD command uses the legacy 01 7E 01 02 ... payloads."""
        p = SonyBRCH900BRBKIP10()
        for cmd_name, payload in p.OSD_PAYLOADS.items():
            assert not payload.startswith(b"\x01\x7e"), f"{cmd_name} still uses legacy 7E payload"
