"""Protocol package for MeowCam Bridge.

Contains input (controller) profiles, output (camera) profiles, and shared VISCA framing utilities.
All profiles implement the interfaces defined in base.py so the bridge core stays generic.
"""

from __future__ import annotations

from typing import Type

from .base import InputProfile, OutputProfile
from .input_ptzoptics import PTZOpticsPTJoyG4SonyVISCAUDP
from .output_sony_brbk import SonyBRCH900BRBKIP10

_INPUT_PROFILES: dict[str, Type[InputProfile]] = {
    "ptzoptics_pt_joy_g4_sony_visca_udp": PTZOpticsPTJoyG4SonyVISCAUDP,
}

_OUTPUT_PROFILES: dict[str, Type[OutputProfile]] = {
    "sony_brc_h900_brbk_ip10": SonyBRCH900BRBKIP10,
}


def list_input_profiles() -> list[str]:
    """Return a list of registered input profile names."""
    return list(_INPUT_PROFILES.keys())


def list_output_profiles() -> list[str]:
    """Return a list of registered output profile names."""
    return list(_OUTPUT_PROFILES.keys())


def get_input_profile(name: str) -> Type[InputProfile]:
    """Return the input profile class for the given name."""
    if name not in _INPUT_PROFILES:
        raise KeyError(f"Unknown input profile: {name!r}")
    return _INPUT_PROFILES[name]


def get_output_profile(name: str) -> Type[OutputProfile]:
    """Return the output profile class for the given name."""
    if name not in _OUTPUT_PROFILES:
        raise KeyError(f"Unknown output profile: {name!r}")
    return _OUTPUT_PROFILES[name]
