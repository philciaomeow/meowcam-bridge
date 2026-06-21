"""VISCA framing utilities shared across profiles.

VISCA is a Sony protocol used by PTZOptics and Sony cameras. Packets are:
  - Header: 0x01 0x00 (VISCA over IP) or absent (raw serial VISCA)
  - Payload: command bytes starting with address byte (0x80 + camera_id)
  - Terminator: 0xFF

VISCA-over-IP adds a 8-byte header:
  0x01 0x00  payload_type(2)  payload_length(2)  seq_number(4)
"""

from __future__ import annotations

import struct
from typing import Final

VISCA_IP_HEADER: Final[bytes] = b"\x01\x00"
VISCA_TERMINATOR: Final[int] = 0xFF
VISCA_COMMAND_TYPE: Final[int] = 0x0200
VISCA_INQUIRY_TYPE: Final[int] = 0x0110
VISCA_REPLY_TYPE: Final[int] = 0x0111


def visca_ip_header(payload_type: int, payload_length: int, seq: int) -> bytes:
    """Build an 8-byte VISCA-over-IP header.

    Format: 0x01 0x00  payload_type(2)  payload_length(2)  seq_number(4)
    """
    return struct.pack(">HHH", 0x0100, payload_type, payload_length) + struct.pack(">I", seq)


def parse_visca_ip_packet(data: bytes) -> tuple[int, int, int, bytes] | None:
    """Parse a VISCA-over-IP packet.

    Returns (payload_type, payload_length, seq, payload_bytes) or None if invalid.
    """
    if len(data) < 8:
        return None
    if data[:2] != VISCA_IP_HEADER:
        return None
    payload_type = struct.unpack(">H", data[2:4])[0]
    payload_length = struct.unpack(">H", data[4:6])[0]
    seq = struct.unpack(">I", data[6:10])[0]
    payload = data[10:10 + payload_length]
    if len(payload) != payload_length:
        return None
    return payload_type, payload_length, seq, payload


def build_visca_ip_packet(payload_type: int, seq: int, payload: bytes) -> bytes:
    """Build a complete VISCA-over-IP packet."""
    header = visca_ip_header(payload_type, len(payload), seq)
    return header + payload


def visca_address(camera_id: int) -> int:
    """Return VISCA address byte for camera_id (1-based)."""
    return 0x80 + camera_id


def visca_payload_terminates(payload: bytes) -> bool:
    """Return True if payload ends with VISCA terminator 0xFF."""
    return len(payload) > 0 and payload[-1] == VISCA_TERMINATOR
