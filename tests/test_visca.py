"""Tests for VISCA framing utilities."""

from __future__ import annotations

import pytest

from meowcam_bridge.protocols.visca import (
    build_visca_ip_packet,
    parse_visca_ip_packet,
    visca_address,
    visca_ip_header,
    visca_payload_terminates,
    VISCA_COMMAND_TYPE,
)


class TestViscaIPHeader:
    def test_header_length(self):
        h = visca_ip_header(VISCA_COMMAND_TYPE, 5, 1)
        assert len(h) == 10

    def test_header_bytes(self):
        h = visca_ip_header(VISCA_COMMAND_TYPE, 5, 1)
        assert h[:2] == b"\x01\x00"


class TestParseViscaIPPacket:
    def test_roundtrip(self):
        payload = b"\x81\x01\x06\x01\x03\x03\xFF"
        packet = build_visca_ip_packet(VISCA_COMMAND_TYPE, 42, payload)
        parsed = parse_visca_ip_packet(packet)
        assert parsed is not None
        payload_type, payload_length, seq, parsed_payload = parsed
        assert payload_type == VISCA_COMMAND_TYPE
        assert payload_length == len(payload)
        assert seq == 42
        assert parsed_payload == payload

    def test_too_short(self):
        assert parse_visca_ip_packet(b"\x01\x00") is None

    def test_bad_header(self):
        assert parse_visca_ip_packet(b"\x00\x00" + b"\x00" * 6) is None

    def test_length_mismatch(self):
        header = visca_ip_header(VISCA_COMMAND_TYPE, 100, 1)
        packet = header + b"short"
        assert parse_visca_ip_packet(packet) is None


class TestViscaAddress:
    def test_camera_1(self):
        assert visca_address(1) == 0x81

    def test_camera_7(self):
        assert visca_address(7) == 0x87


class TestViscaPayloadTerminates:
    def test_terminates(self):
        assert visca_payload_terminates(b"\x81\xFF") is True

    def test_no_terminator(self):
        assert visca_payload_terminates(b"\x81\x01") is False

    def test_empty(self):
        assert visca_payload_terminates(b"") is False
