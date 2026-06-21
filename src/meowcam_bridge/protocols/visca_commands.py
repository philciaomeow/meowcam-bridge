"""VISCA command payloads shared across profiles.

Provides builders for common VISCA commands so profiles can construct payloads
without hard-coding byte sequences everywhere.
"""

from __future__ import annotations


def visca_version_inquiry(camera_id: int = 1) -> bytes:
    """Return VISCA version inquiry payload.

    Format: 0x8x 0x09 0x00 0x02 0xFF
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x09, 0x00, 0x02, 0xFF])


def visca_pan_tilt_stop(camera_id: int = 1) -> bytes:
    """Return VISCA pan/tilt stop command payload.

    Format: 0x8x 0x01 0x06 0x01 VV WW 0x03 0x03 0xFF
    VV = pan speed (0x03 = stop), WW = tilt speed (0x03 = stop)
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x01, 0x06, 0x01, 0x03, 0x03, 0x03, 0x03, 0xFF])


def visca_pan_tilt_direction(camera_id: int = 1, pan_speed: int = 0, tilt_speed: int = 0,
                             pan_dir: int = 0, tilt_dir: int = 0) -> bytes:
    """Return VISCA pan/tilt direction command payload.

    pan_dir/tilt_dir: 1=left/up, 2=right/down, 3=stop
    pan_speed/tilt_speed: 0x01-0x18 (1-24)
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x01, 0x06, 0x01, pan_speed & 0xFF, tilt_speed & 0xFF,
                  pan_dir & 0xFF, tilt_dir & 0xFF, 0xFF])


def visca_zoom(camera_id: int = 1, speed: int = 0) -> bytes:
    """Return VISCA zoom command payload.

    speed: 0=stop, 0x01-0x07=tele (in), 0x09-0x0F=wide (out)
    Bit 3 (0x08) indicates direction: 0=tele, 1=wide.
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x01, 0x04, 0x07, speed & 0xFF, 0xFF])


def visca_focus(camera_id: int = 1, speed: int = 0) -> bytes:
    """Return VISCA focus command payload.

    speed: 0=stop, 0x01-0x07=near, 0x09-0x0F=far
    Bit 3 (0x08) indicates direction: 0=near, 1=far.
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x01, 0x04, 0x08, speed & 0xFF, 0xFF])


def visca_autofocus(camera_id: int = 1, on: bool = True) -> bytes:
    """Return VISCA autofocus on/off payload."""
    addr = 0x80 + camera_id
    val = 0x02 if on else 0x03
    return bytes([addr, 0x01, 0x04, 0x38, val, 0xFF])


def visca_preset_recall(camera_id: int = 1, preset: int = 0) -> bytes:
    """Return VISCA preset recall payload.

    preset: 0-15 (VISCA preset numbers are 0-based internally)
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x01, 0x06, 0x01, preset & 0x0F, 0xFF])


def visca_preset_save(camera_id: int = 1, preset: int = 0) -> bytes:
    """Return VISCA preset save payload.

    preset: 0-15 (VISCA preset numbers are 0-based internally)
    """
    addr = 0x80 + camera_id
    return bytes([addr, 0x01, 0x04, 0x3F, 0x01, preset & 0x0F, 0xFF])
