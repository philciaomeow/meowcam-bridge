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

    # Preset labels: index 0 = preset 1, etc.
    preset_labels: list[str] = Field(default_factory=lambda: [f"Preset {i}" for i in range(1, 17)])

    @field_validator("preset_labels")
    @classmethod
    def _limit_presets(cls, v: list[str]) -> list[str]:
        return v[:16]


class BridgeConfig(BaseModel):
    """Top-level bridge configuration."""

    bridge_ip: str = "0.0.0.0"
    bridge_ui_port: int = Field(default=8080, ge=1, le=65535)
    controller_bind_ip: str = "0.0.0.0"
    routes: list[CameraRoute] = Field(default_factory=list)

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
