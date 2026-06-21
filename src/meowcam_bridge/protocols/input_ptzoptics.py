"""Input profile: PTZOptics PT-JOY-G4 sending Sony VISCA over UDP.

Decodes VISCA-over-IP packets from the controller and rewrites sequence numbers
so the bridge core can manage its own per-camera sequence space.
"""

from __future__ import annotations

from typing import Any

from .base import InputProfile
from .visca import parse_visca_ip_packet, build_visca_ip_packet


class PTZOpticsPTJoyG4SonyVISCAUDP(InputProfile):
    """PTZOptics PT-JOY-G4 controller profile.

    Expects VISCA-over-IP packets on UDP. Sequence numbers from the controller
    are preserved in the decoded command and rewritten on reply so the controller
    sees consistent responses.
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

    def decode(self, data: bytes, source_addr: tuple[str, int]) -> dict[str, Any] | None:
        parsed = parse_visca_ip_packet(data)
        if parsed is None:
            return None
        payload_type, payload_length, seq, payload = parsed
        return {
            "type": "visca_command",
            "payload_type": payload_type,
            "payload_length": payload_length,
            "seq": seq,
            "payload": payload,
            "source_addr": source_addr,
        }

    def encode_reply(self, reply: dict[str, Any], original_cmd: dict[str, Any]) -> bytes | None:
        payload = reply.get("payload")
        if payload is None:
            return None
        seq = original_cmd.get("seq", 0)
        payload_type = reply.get("payload_type", 0x0111)  # default reply type
        return build_visca_ip_packet(payload_type, seq, payload)

    def supports(self, capability: str) -> bool:
        return capability in self.CAPABILITIES
