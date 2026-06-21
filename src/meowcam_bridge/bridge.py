"""Bridge core stub.

Will contain the asyncio UDP relay logic, route table, and sequence mapping.
For now this module exists as a placeholder so imports resolve.
"""

from __future__ import annotations

from typing import Any

from .config import BridgeConfig, CameraRoute


class BridgeCore:
    """Placeholder bridge core."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._route_states: dict[int, dict[str, Any]] = {}

    def route_state(self, route_index: int) -> dict[str, Any]:
        """Return (and create if needed) per-route mutable state."""
        if route_index not in self._route_states:
            self._route_states[route_index] = {}
        return self._route_states[route_index]

    async def start(self) -> None:
        """Start UDP listeners and relay tasks."""
        raise NotImplementedError("BridgeCore.start is a stub")

    async def stop(self) -> None:
        """Stop all listeners and relay tasks."""
        raise NotImplementedError("BridgeCore.stop is a stub")
