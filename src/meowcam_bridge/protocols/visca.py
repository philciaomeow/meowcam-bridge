"""VISCA framing utilities shared across profiles.

Sony VISCA-over-IP packet format (8-byte header + payload):
  bytes 0-1: message type (0x0100=command, 0x0110=inquiry, 0x0111=reply, 0x0120=setting, 0x0200=control, 0x0201=control_reply)
  bytes 2-3: payload length (big-endian uint16)
  bytes 4-7: sequence number (big-endian uint32)
  bytes 8+:  payload data (VISCA command bytes, terminated by 0xFF)
"""

from __future__ import annotations

import struct
from typing import Final

VISCA_TERMINATOR: Final[int] = 0xFF

# Sony VISCA-over-IP message types
VISCA_COMMAND_TYPE: Final[int] = 0x0100
VISCA_INQUIRY_TYPE: Final[int] = 0x0110
VISCA_REPLY_TYPE: Final[int] = 0x0111
VISCA_SETTING_TYPE: Final[int] = 0x0120
VISCA_CONTROL_TYPE: Final[int] = 0x0200
VISCA_CONTROL_REPLY_TYPE: Final[int] = 0x0201

# All valid Sony VISCA-over-IP message types
_VALID_MSG_TYPES: Final[set[int]] = {
    VISCA_COMMAND_TYPE, VISCA_INQUIRY_TYPE, VISCA_REPLY_TYPE,
    VISCA_SETTING_TYPE, VISCA_CONTROL_TYPE, VISCA_CONTROL_REPLY_TYPE,
}


def visca_ip_header(payload_type: int, payload_length: int, seq: int) -> bytes:
    """Build an 8-byte VISCA-over-IP header."""
    return struct.pack(">HHI", payload_type, payload_length, seq)


def parse_visca_ip_packet(data: bytes) -> tuple[int, int, int, bytes] | None:
    """Parse a VISCA-over-IP packet.

    Returns (payload_type, payload_length, seq, payload_bytes) or None if invalid.
    """
    if len(data) < 8:
        return None
    payload_type = struct.unpack(">H", data[0:2])[0]
    if payload_type not in _VALID_MSG_TYPES:
        return None
    payload_length = struct.unpack(">H", data[2:4])[0]
    seq = struct.unpack(">I", data[4:8])[0]
    payload = data[8:8 + payload_length]
    if len(payload) != payload_length:
        # If we don't have enough bytes, take what we can
        # (some implementations may not include the terminator in the length)
        payload = data[8:]
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