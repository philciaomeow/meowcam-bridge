"""Output profile: Sony BRC-H900 with BRBK-IP10 IP card.

Key behaviours:
- Camera listens on UDP 52381.
- Camera replies from UDP 65000 back to the source UDP 52381.
- The bridge must therefore send from local UDP source port 52381.
- Address byte is forced to 0x81 (camera ID 1) regardless of controller address.
- Sequence numbers are rewritten to clean incrementing values per route.
"""

from __future__ import annotations

from typing import Any

from .base import OutputProfile
from .visca import (
    build_visca_ip_packet,
    parse_visca_ip_packet,
    visca_address,
    VISCA_COMMAND_TYPE,
    VISCA_INQUIRY_TYPE,
    VISCA_REPLY_TYPE,
)


class SonyBRCH900BRBKIP10(OutputProfile):
    """Sony BRC-H900 + BRBK-IP10 output profile.

    Forces VISCA address byte to 0x81 and manages per-route sequence numbers.
    Expects replies from camera source port 65000 to local port 52381.
    """

    name = "sony_brc_h900_brbk_ip10"
    description = "Sony BRC-H900 with BRBK-IP10 IP card (VISCA/IP, UDP 52381/65000)"

    SOURCE_PORT: int = 52381
    CAMERA_REPLY_PORT: int = 65000
    FORCED_CAMERA_ID: int = 1

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

    # OSD payload overrides for BRC-H900.
    # These match the Sony VISCA OSD commands:
    #   menu_open  = 01 06 06 02 FF  (OSD on)
    #   menu_close = 01 06 06 03 FF  (OSD off)
    #   menu_enter = 01 06 06 05 FF  (OSD enter / select)
    #   menu_back  = 01 06 06 04 FF  (OSD back / return)
    # Other cameras with different OSD semantics can override these in their
    # own OutputProfile subclass.
    OSD_PAYLOADS: dict[str, bytes] = {
        "menu_open":  bytes([0x01, 0x06, 0x06, 0x02, 0xFF]),
        "menu_close": bytes([0x01, 0x06, 0x06, 0x03, 0xFF]),
        "menu_enter": bytes([0x01, 0x06, 0x06, 0x05, 0xFF]),
        "menu_back":  bytes([0x01, 0x06, 0x06, 0x04, 0xFF]),
    }

    def _next_seq(self, route_state: dict[str, Any]) -> int:
        seq = route_state.get("sony_seq", 0)
        seq = (seq + 1) & 0xFFFFFFFF
        if seq == 0:
            seq = 1
        route_state["sony_seq"] = seq
        return seq

    def _force_address(self, payload: bytes) -> bytes:
        """Return payload addressed to camera ID 1.

        Controller-originated VISCA payloads already include an address byte
        (0x81-0x88), so rewrite that byte. Bridge-generated UI/API commands are
        intentionally stored without an address byte and begin with the VISCA
        command category (usually 0x01 or 0x09), so prepend the address instead.
        """
        if not payload:
            return payload
        addr = visca_address(self.FORCED_CAMERA_ID)
        if 0x81 <= payload[0] <= 0x88:
            return bytes([addr]) + payload[1:]
        return bytes([addr]) + payload

    def encode(self, cmd: dict[str, Any], route_state: dict[str, Any]) -> bytes | None:
        payload = cmd.get("payload")
        if payload is None:
            return None
        payload = self._force_address(payload)
        payload_type = cmd.get("payload_type", VISCA_COMMAND_TYPE)
        seq = self._next_seq(route_state)
        return build_visca_ip_packet(payload_type, seq, payload)

    def decode_reply(self, data: bytes, route_state: dict[str, Any]) -> dict[str, Any] | None:
        parsed = parse_visca_ip_packet(data)
        if parsed is None:
            return None
        payload_type, payload_length, seq, payload = parsed
        return {
            "type": "visca_reply",
            "payload_type": payload_type,
            "payload_length": payload_length,
            "seq": seq,
            "payload": payload,
        }

    def source_port(self) -> int:
        return self.SOURCE_PORT

    def supports(self, capability: str) -> bool:
        return capability in self.CAPABILITIES
