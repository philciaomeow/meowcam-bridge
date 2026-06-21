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
