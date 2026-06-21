"""Tests for PTZOptics PT-JOY-G4 input profile.

Covers:
- VISCA-over-IP packet decoding
- Raw VISCA packet detection and decoding
- Controller sequence preservation in replies
- Capability queries
- Profile registry discovery
"""

from __future__ import annotations

import pytest

from meowcam_bridge.protocols import (
    get_input_profile,
    get_output_profile,
    list_input_profiles,
    list_output_profiles,
)
from meowcam_bridge.protocols.input_ptzoptics import PTZOpticsPTJoyG4SonyVISCAUDP
from meowcam_bridge.protocols.visca import build_visca_ip_packet, VISCA_COMMAND_TYPE


class TestPTZOpticsDecodeVISCAIP:
    """VISCA-over-IP framing detection."""

    def test_decode_visca_ip_packet(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        payload = b"\x81\x01\x06\x01\x03\x03\xFF"
        packet = build_visca_ip_packet(VISCA_COMMAND_TYPE, 42, payload)
        decoded = p.decode(packet, ("192.168.1.50", 12345))
        assert decoded is not None
        assert decoded["type"] == "visca_command"
        assert decoded["framing"] == "visca_ip"
        assert decoded["seq"] == 42
        assert decoded["payload"] == payload
        assert decoded["source_addr"] == ("192.168.1.50", 12345)

    def test_decode_visca_ip_inquiry(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        payload = b"\x81\x09\x00\x02\xFF"  # version inquiry
        packet = build_visca_ip_packet(0x0110, 99, payload)
        decoded = p.decode(packet, ("192.168.1.50", 52380))
        assert decoded is not None
        assert decoded["payload_type"] == 0x0110
        assert decoded["seq"] == 99

    def test_decode_too_short_returns_none(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.decode(b"\x01\x00", ("192.168.1.50", 12345)) is None

    def test_decode_bad_header_returns_none(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.decode(b"\x00\x00" + b"\x00" * 20, ("192.168.1.50", 12345)) is None


class TestPTZOpticsDecodeRawVISCA:
    """Raw VISCA (no IP header) detection."""

    def test_decode_raw_visca(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        raw = b"\x81\x01\x06\x01\x03\x03\xFF"
        decoded = p.decode(raw, ("192.168.1.50", 52380))
        assert decoded is not None
        assert decoded["type"] == "visca_command"
        assert decoded["framing"] == "raw"
        assert decoded["seq"] == 0
        assert decoded["payload"] == raw

    def test_decode_raw_different_address(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        raw = b"\x88\x01\x06\x01\x03\x03\xFF"  # camera 8
        decoded = p.decode(raw, ("192.168.1.50", 52380))
        assert decoded is not None
        assert decoded["framing"] == "raw"
        assert decoded["payload"][0] == 0x88

    def test_decode_raw_no_terminator_returns_none(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        raw = b"\x81\x01\x06\x01\x03\x03"  # missing 0xFF
        assert p.decode(raw, ("192.168.1.50", 52380)) is None

    def test_decode_raw_bad_address_returns_none(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        raw = b"\x80\x01\x06\x01\x03\x03\xFF"  # 0x80 is broadcast, not a camera address
        assert p.decode(raw, ("192.168.1.50", 52380)) is None

    def test_decode_raw_too_short_returns_none(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.decode(b"\x81\xFF", ("192.168.1.50", 52380)) is None
        assert p.decode(b"\x81", ("192.168.1.50", 52380)) is None

    def test_visca_ip_header_not_mistaken_for_raw(self):
        """A packet with VISCA-over-IP header must be parsed as visca_ip, not raw."""
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        payload = b"\x81\x01\x06\x01\x03\x03\xFF"
        packet = build_visca_ip_packet(VISCA_COMMAND_TYPE, 5, payload)
        decoded = p.decode(packet, ("192.168.1.50", 52380))
        assert decoded is not None
        assert decoded["framing"] == "visca_ip"
        assert decoded["seq"] == 5


class TestPTZOpticsSequencePreservation:
    """Controller sequence numbers must be preserved in replies."""

    def test_encode_reply_preserves_visca_ip_sequence(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        payload = b"\x81\x01\x06\x01\x03\x03\xFF"
        packet = build_visca_ip_packet(VISCA_COMMAND_TYPE, 123, payload)
        original_cmd = p.decode(packet, ("192.168.1.50", 52380))
        assert original_cmd is not None
        assert original_cmd["seq"] == 123

        reply = {"payload": b"\x81\x50\xFF", "payload_type": 0x0111}
        encoded = p.encode_reply(reply, original_cmd)
        assert encoded is not None
        # Should be VISCA-over-IP with seq=123
        from meowcam_bridge.protocols.visca import parse_visca_ip_packet
        parsed = parse_visca_ip_packet(encoded)
        assert parsed is not None
        _, _, seq, reply_payload = parsed
        assert seq == 123
        assert reply_payload == b"\x81\x50\xFF"

    def test_encode_reply_raw_returns_payload_only(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        raw = b"\x81\x01\x06\x01\x03\x03\xFF"
        original_cmd = p.decode(raw, ("192.168.1.50", 52380))
        assert original_cmd is not None
        assert original_cmd["framing"] == "raw"

        reply = {"payload": b"\x81\x50\xFF"}
        encoded = p.encode_reply(reply, original_cmd)
        assert encoded == b"\x81\x50\xFF"

    def test_encode_reply_no_payload_returns_none(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        reply = {"payload": None}
        original_cmd = {"framing": "visca_ip", "seq": 1}
        assert p.encode_reply(reply, original_cmd) is None


class TestPTZOpticsCapabilities:
    def test_supports_pan_tilt(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("pan_tilt") is True

    def test_supports_zoom(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("zoom") is True

    def test_supports_preset_recall(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("preset_recall") is True

    def test_supports_preset_save(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("preset_save") is True

    def test_supports_autofocus(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("autofocus") is True

    def test_supports_menu_osd(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("menu_osd") is True

    def test_supports_inquiry(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("inquiry") is True

    def test_does_not_support_unknown(self):
        p = PTZOpticsPTJoyG4SonyVISCAUDP()
        assert p.supports("nonexistent") is False


class TestProfileRegistry:
    """Profile discovery via the protocols package registry."""

    def test_list_input_profiles(self):
        profiles = list_input_profiles()
        assert "ptzoptics_pt_joy_g4_sony_visca_udp" in profiles

    def test_list_output_profiles(self):
        profiles = list_output_profiles()
        assert "sony_brc_h900_brbk_ip10" in profiles

    def test_get_input_profile(self):
        cls = get_input_profile("ptzoptics_pt_joy_g4_sony_visca_udp")
        assert cls is PTZOpticsPTJoyG4SonyVISCAUDP

    def test_get_output_profile(self):
        cls = get_output_profile("sony_brc_h900_brbk_ip10")
        from meowcam_bridge.protocols.output_sony_brbk import SonyBRCH900BRBKIP10
        assert cls is SonyBRCH900BRBKIP10

    def test_get_unknown_input_raises(self):
        with pytest.raises(KeyError, match="Unknown input profile"):
            get_input_profile("nonexistent")

    def test_get_unknown_output_raises(self):
        with pytest.raises(KeyError, match="Unknown output profile"):
            get_output_profile("nonexistent")
