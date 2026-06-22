"""Configuration models for MeowCam Bridge.

Supports up to 8 camera routes. Profiles are referenced by name and resolved at runtime
so the config file stays serialisable and human-editable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


MAX_ROUTES: int = 8
DEFAULT_CONTROLLER_PORT: int = 52381


class CameraVideo(BaseModel):
    """Video capture settings for a camera route."""

    enabled: bool = False
    source_type: Literal["none", "ndi", "usb", "testpattern"] = "none"
    ndi_source_name: str = ""
    usb_device_index: int = 0
    resolution: str = "640x360"
    frame_rate: int = 8
    jpeg_quality: int = 60
    # Region of interest crop — allows multiple cameras to share one NDI feed
    # and show different portions of it. Values are fractions 0.0-1.0.
    # If all four are 0, no crop is applied (full frame).
    crop_x: float = Field(default=0.0, ge=0.0, le=1.0)
    crop_y: float = Field(default=0.0, ge=0.0, le=1.0)
    crop_w: float = Field(default=0.0, ge=0.0, le=1.0)
    crop_h: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("resolution")
    @classmethod
    def _parse_resolution(cls, v: str) -> str:
        if "x" not in v:
            raise ValueError("resolution must be in WxH format (e.g. 640x360)")
        w, h = v.split("x")
        if not (w.isdigit() and h.isdigit()):
            raise ValueError("resolution must be in WxH format (e.g. 640x360)")
        return v


class CameraRoute(BaseModel):
    """A single controller-to-camera route."""

    enabled: bool = False
    label: str = "Camera"
    incoming_port: int = Field(default=DEFAULT_CONTROLLER_PORT, ge=1, le=65535)
    input_profile: str = "ptzoptics_pt_joy_g4_sony_visca_udp"
    output_profile: str = "sony_brc_h900_brbk_ip10"
    camera_ip: str = "192.168.1.100"
    camera_port: int = Field(default=52381, ge=1, le=65535)
    status: Literal["unknown", "ok", "error", "disabled"] = "unknown"
    movement_speed: Literal["slow", "medium", "fast"] = "medium"

    # Preset labels: index 0 = preset 1, etc.
    preset_labels: list[str] = Field(default_factory=lambda: [f"Preset {i}" for i in range(1, 17)])

    # Video capture settings
    video: CameraVideo = Field(default_factory=CameraVideo)

    @field_validator("preset_labels")
    @classmethod
    def _limit_presets(cls, v: list[str]) -> list[str]:
        return v[:16]


class AtemConfig(BaseModel):
    """ATEM switcher connection and SuperSource configuration.

    input_mapping maps route_index (0-7) to ATEM SDI input number (1-20).
    Only the first 4 entries are used for SuperSource boxes; the remaining
    entries are reserved for future use (e.g. PGM/PVW source mapping).
    """

    enabled: bool = False
    atem_ip: str = "192.168.1.240"
    supersource_aux_output: int = Field(default=1, ge=1, le=6)
    input_mapping: list[int] = Field(
        default_factory=lambda: [1, 2, 3, 4, 5, 6, 7, 8]
    )

    @field_validator("input_mapping")
    @classmethod
    def _validate_input_mapping(cls, v: list[int]) -> list[int]:
        if len(v) != MAX_ROUTES:
            raise ValueError(f"input_mapping must have exactly {MAX_ROUTES} entries")
        for inp in v:
            if not (1 <= inp <= 20):
                raise ValueError(f"ATEM SDI input must be 1-20, got {inp}")
        return v


class BridgeConfig(BaseModel):
    """Top-level bridge configuration."""

    bridge_ip: str = "0.0.0.0"
    bridge_ui_port: int = Field(default=8080, ge=1, le=65535)
    controller_bind_ip: str = "0.0.0.0"
    routes: list[CameraRoute] = Field(default_factory=list)
    atem: AtemConfig = Field(default_factory=AtemConfig)

    @field_validator("routes")
    @classmethod
    def _max_routes(cls, v: list[CameraRoute]) -> list[CameraRoute]:
        if len(v) > MAX_ROUTES:
            raise ValueError(f"Maximum {MAX_ROUTES} camera routes supported")
        return v

    def enabled_routes(self) -> list[tuple[int, CameraRoute]]:
        """Return (index, route) tuples for enabled routes."""
        return [(i, r) for i, r in enumerate(self.routes) if r.enabled]

    @classmethod
    def load(cls, path: str | Path) -> "BridgeConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            self.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
