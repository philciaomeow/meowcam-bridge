"""Input profile: PTZOptics PT-JOY-G4 sending Sony VISCA over UDP.

Decodes both raw VISCA packets (serial-style, no header) and VISCA-over-IP packets
from the controller. Preserves controller sequence numbers and framing metadata
so replies can be returned in the controller's expected format.
"""

from __future__ import annotations

from typing import Any

from .base import InputProfile
from .visca import (
    VISCA_TERMINATOR,
    _VALID_MSG_TYPES,
    build_visca_ip_packet,
    parse_visca_ip_packet,
)


class PTZOpticsPTJoyG4SonyVISCAUDP(InputProfile):
    """PTZOptics PT-JOY-G4 controller profile.

    Handles two input formats the controller may send:
      1. VISCA-over-IP: 8-byte header (0x01 0x00 ...) + payload
      2. Raw VISCA: payload bytes only, starting with address byte + 0xFF terminator

    For VISCA-over-IP, sequence numbers from the controller are preserved.
    For raw VISCA, a synthetic sequence of 0 is used (no sequence concept in raw).

    Replies are encoded back in the same format as the incoming command.
    """

    name = "ptzoptics_pt_joy_g4_sony_visca_udp"
    description = "PTZOptics PT-JOY-G4 controller sending Sony VISCA over UDP"

    CAPABILITIES: set[str] = {
        "pan_tilt",
        "zoom",
        "focus",
        "preset_recall",
        "preset_save",
        "autofocus",
        "menu_osd",
        "inquiry",
    }

    def _is_raw_visca(self, data: bytes) -> bool:
        """Detect raw VISCA packet (no IP header, starts with address byte, ends with 0xFF)."""
        if len(data) < 3:
            return False
        # Must start with a valid VISCA address byte (0x81-0x88)
        if not (0x81 <= data[0] <= 0x88):
            return False
        # Must end with VISCA terminator
        if data[-1] != VISCA_TERMINATOR:
            return False
        # Must NOT have VISCA-over-IP header
        if data[:2] == b'\x01\x00' or (len(data) >= 2 and int.from_bytes(data[:2], 'big') in _VALID_MSG_TYPES):
            return False
        return True

    def decode(self, data: bytes, source_addr: tuple[str, int]) -> dict[str, Any] | None:
        # Try VISCA-over-IP first
        parsed = parse_visca_ip_packet(data)
        if parsed is not None:
            payload_type, payload_length, seq, payload = parsed
            return {
                "type": "visca_command",
                "framing": "visca_ip",
                "payload_type": payload_type,
                "payload_length": payload_length,
                "seq": seq,
                "payload": payload,
                "source_addr": source_addr,
            }

        # Fall back to raw VISCA
        if self._is_raw_visca(data):
            return {
                "type": "visca_command",
                "framing": "raw",
                "payload_type": None,
                "payload_length": len(data),
                "seq": 0,
                "payload": data,
                "source_addr": source_addr,
            }

        return None

    def encode_reply(self, reply: dict[str, Any], original_cmd: dict[str, Any]) -> bytes | None:
        payload = reply.get("payload")
        if payload is None:
            return None

        framing = original_cmd.get("framing", "visca_ip")

        if framing == "raw":
            # Raw VISCA: just return payload bytes as-is
            return payload

        # VISCA-over-IP: rebuild with controller's original sequence number
        seq = original_cmd.get("seq", 0)
        payload_type = reply.get("payload_type", 0x0111)  # default reply type
        return build_visca_ip_packet(payload_type, seq, payload)

    def supports(self, capability: str) -> bool:
        return capability in self.CAPABILITIES
