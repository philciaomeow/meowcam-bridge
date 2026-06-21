"""Input profile: PTZOptics PT-JOY-G4 in VISCA(UDP) mode.

When the controller is set to "VISCA(UDP)" protocol (instead of "SONY VISCA(UDP)"),
it allows custom port numbers. This profile handles the raw VISCA packets that
the controller sends in this mode.

The controller may send:
  1. Raw VISCA bytes (address byte + command + 0xFF terminator) — most common
  2. VISCA-over-IP packets (8-byte Sony header) — some firmware versions

Both are auto-detected and handled. Replies are sent back in the matching format.
"""

from __future__ import annotations

from .input_ptzoptics import PTZOpticsPTJoyG4SonyVISCAUDP


class PTZOpticsPTJoyG4VISCAUDP(PTZOpticsPTJoyG4SonyVISCAUDP):
    """PTZOptics PT-JOY-G4 controller in VISCA(UDP) mode (custom port).

    Identical packet handling to the SONY VISCA(UDP) profile — both auto-detect
    raw VISCA and Sony-framed packets. The distinction is purely semantic:
    this profile is selected when the controller is configured for "VISCA(UDP)"
    protocol mode, which allows custom port numbers per camera channel.
    """

    name = "ptzoptics_pt_joy_g4_visca_udp"
    description = "PTZOptics PT-JOY-G4 in VISCA(UDP) mode (custom port, raw VISCA)"
