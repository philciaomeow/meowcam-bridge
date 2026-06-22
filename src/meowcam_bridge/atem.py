"""ATEM switcher integration — connection, SuperSource 2x2, PGM/PVW tally.

Uses PyATEMMax for the underlying UDP protocol. The ATEM connection runs in
a background thread (PyATEMMax manages its own reconnect loop), and we read
state from the switcher's thread-safe state objects.

Thread-safety: PyATEMMax state objects are updated by the protocol thread and
are safe to read from the main thread. We use a threading.Lock for operations
that mutate switcher settings (setSuperSource*, setAuxSourceInput) to prevent
interleaved sends from concurrent API calls.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .config import AtemConfig

logger = logging.getLogger(__name__)

# 2x2 grid positions (ATEM coordinate system: X -48..48, Y -27..27)
# Size 0.5 = each box covers half the frame width
GRID_2X2_POSITIONS = [
    {"x": -12.0, "y": -6.75, "size": 0.5},  # Box 0: top-left
    {"x": 12.0, "y": -6.75, "size": 0.5},   # Box 1: top-right
    {"x": -12.0, "y": 6.75, "size": 0.5},   # Box 2: bottom-left
    {"x": 12.0, "y": 6.75, "size": 0.5},    # Box 3: bottom-right
]

NUM_BOXES = 4
NUM_INPUTS = 20  # ATEM 4 M/E Broadcast Studio 4K has 20 SDI inputs


class ATEMConnectionError(Exception):
    """Raised when the ATEM switcher is not connected or reachable."""


class ATEMManager:
    """Manages the ATEM switcher connection and provides high-level operations.

    Lifecycle:
        manager = ATEMManager(config)
        manager.connect()      # starts background connection thread
        ...
        manager.disconnect()   # stops background thread

    All read methods raise ATEMConnectionError if not connected.
    Write methods are guarded by a lock to prevent interleaved sends.
    """

    def __init__(self, config: AtemConfig) -> None:
        self._config = config
        self._switcher: Any = None  # PyATEMMax.ATEMMax instance
        self._connected = False
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        """True if the switcher is connected and ready for commands."""
        return self._connected and self._switcher is not None

    def connect(self) -> None:
        """Connect to the ATEM switcher. Non-blocking — PyATEMMax auto-reconnects."""
        if self._switcher is not None:
            logger.warning("ATEM already connected, disconnecting first")
            self.disconnect()

        try:
            import PyATEMMax
        except ImportError:
            raise ATEMConnectionError("PyATEMMax is not installed (pip install PyATEMMax)")

        self._switcher = PyATEMMax.ATEMMax()
        self._switcher.connect(self._config.atem_ip)
        # waitForConnection blocks until the first handshake completes or times out
        self._switcher.waitForConnection()
        self._connected = True
        logger.info("ATEM connected to %s", self._config.atem_ip)

    def disconnect(self) -> None:
        """Disconnect from the ATEM switcher."""
        if self._switcher is not None:
            try:
                self._switcher.disconnect()
            except Exception:
                pass
            self._switcher = None
        self._connected = False
        logger.info("ATEM disconnected")

    def _ensure_connected(self) -> Any:
        """Return the switcher or raise ATEMConnectionError."""
        if not self.connected or self._switcher is None:
            raise ATEMConnectionError("ATEM switcher not connected")
        return self._switcher

    # ------------------------------------------------------------------
    # SuperSource 2x2 grid
    # ------------------------------------------------------------------

    def configure_supersource_2x2(self, input_mapping: list[int] | None = None) -> dict:
        """Configure SuperSource as a 2x2 grid with 4 SDI inputs.

        Args:
            input_mapping: list of 4 ATEM SDI input numbers (1-20).
                           If None, uses the first 4 entries from config.input_mapping.

        Returns:
            dict describing the applied configuration for each box.
        """
        sw = self._ensure_connected()

        from PyATEMMax.ATEMProtocolEnums import ATEMVideoSources, ATEMBoxes

        if input_mapping is None:
            input_mapping = self._config.input_mapping[:4]
        if len(input_mapping) < NUM_BOXES:
            raise ValueError(f"Need {NUM_BOXES} input mappings, got {len(input_mapping)}")

        result = []
        with self._lock:
            # Disable the foreground key overlay — we just want 4 boxes
            sw.setSuperSourceForeground(False)

            for i in range(NUM_BOXES):
                box = [ATEMBoxes.box1, ATEMBoxes.box2, ATEMBoxes.box3, ATEMBoxes.box4][i]
                pos = GRID_2X2_POSITIONS[i]
                source = getattr(ATEMVideoSources, f"input{input_mapping[i]}")

                sw.setSuperSourceBoxParametersEnabled(box, True)
                sw.setSuperSourceBoxParametersInputSource(box, source)
                sw.setSuperSourceBoxParametersPositionX(box, pos["x"])
                sw.setSuperSourceBoxParametersPositionY(box, pos["y"])
                sw.setSuperSourceBoxParametersSize(box, pos["size"])
                sw.setSuperSourceBoxParametersCropped(box, False)

                result.append({
                    "box": i,
                    "enabled": True,
                    "input_source": input_mapping[i],
                    "position_x": pos["x"],
                    "position_y": pos["y"],
                    "size": pos["size"],
                })

        logger.info("SuperSource 2x2 configured with inputs %s", input_mapping)
        return {"boxes": result}

    def route_supersource_to_aux(self, aux_output: int | None = None) -> dict:
        """Route the SuperSource output to an AUX output.

        Args:
            aux_output: AUX output number (1-6). If None, uses config value.

        Returns:
            dict with the aux_output and source routed.
        """
        sw = self._ensure_connected()

        from PyATEMMax.ATEMProtocolEnums import ATEMVideoSources, ATEMAUXChannels

        if aux_output is None:
            aux_output = self._config.supersource_aux_output
        if not (1 <= aux_output <= 6):
            raise ValueError(f"AUX output must be 1-6, got {aux_output}")

        aux_channel = getattr(ATEMAUXChannels, f"auxChannel{aux_output}")
        with self._lock:
            sw.setAuxSourceInput(aux_channel, ATEMVideoSources.superSource)

        logger.info("SuperSource routed to AUX %d", aux_output)
        return {"aux_output": aux_output, "source": "superSource"}

    def get_supersource_state(self) -> dict:
        """Read current SuperSource box configuration from the switcher."""
        sw = self._ensure_connected()

        boxes = []
        for i in range(NUM_BOXES):
            bp = sw.superSource.boxParameters[i]
            src_val = bp.inputSource.value if bp.inputSource and bp.inputSource.value is not None else None
            boxes.append({
                "box": i,
                "enabled": bp.enabled,
                "input_source": src_val,
                "position_x": bp.position.x,
                "position_y": bp.position.y,
                "size": bp.size,
            })

        # Read AUX routing for the configured output
        aux_output = self._config.supersource_aux_output
        aux_input = None
        try:
            aux_src = sw.auxSource[aux_output - 1].input
            aux_input = aux_src.value if aux_src and aux_src.value is not None else None
        except Exception:
            pass

        return {
            "boxes": boxes,
            "aux_output": aux_output,
            "aux_source": aux_input,
        }

    def toggle_box(self, box_index: int, enabled: bool | None = None) -> dict:
        """Toggle a single SuperSource box on/off.

        Args:
            box_index: 0-3 for boxes 1-4.
            enabled: If None, toggles current state.

        Returns:
            dict with box index and new enabled state.
        """
        sw = self._ensure_connected()

        from PyATEMMax.ATEMProtocolEnums import ATEMBoxes

        if not (0 <= box_index < NUM_BOXES):
            raise ValueError(f"Box index must be 0-{NUM_BOXES - 1}, got {box_index}")

        box = [ATEMBoxes.box1, ATEMBoxes.box2, ATEMBoxes.box3, ATEMBoxes.box4][box_index]

        with self._lock:
            if enabled is None:
                # Read current state and toggle
                current = sw.superSource.boxParameters[box_index].enabled
                enabled = not current
            sw.setSuperSourceBoxParametersEnabled(box, enabled)

        logger.info("SuperSource box %d %s", box_index, "enabled" if enabled else "disabled")
        return {"box": box_index, "enabled": enabled}

    # ------------------------------------------------------------------
    # PGM/PVW tally
    # ------------------------------------------------------------------

    def get_tally(self, me_index: int = 0) -> dict:
        """Read current PGM/PVW source for the given M/E row.

        Args:
            me_index: M/E row index (0-3 for the 4 M/E Broadcast Studio 4K).

        Returns:
            {"pgm_source": int, "pvw_source": int, "me_index": int}
        """
        sw = self._ensure_connected()

        pgm = sw.programInput[me_index].videoSource
        pvw = sw.previewInput[me_index].videoSource

        pgm_val = pgm.value if pgm and pgm.value is not None else None
        pvw_val = pvw.value if pvw and pvw.value is not None else None

        return {
            "me_index": me_index,
            "pgm_source": pgm_val,
            "pvw_source": pvw_val,
        }

    def get_inputs(self) -> list[dict]:
        """Read input labels for all 20 SDI inputs.

        Returns:
            [{"source": N, "long_name": "...", "short_name": "..."}, ...]
        """
        sw = self._ensure_connected()

        inputs = []
        for i in range(1, NUM_INPUTS + 1):
            try:
                props = sw.inputProperties[i]
                long_name = props.longName if props.longName else ""
                short_name = props.shortName if props.shortName else ""
                inputs.append({
                    "source": i,
                    "long_name": long_name,
                    "short_name": short_name,
                })
            except (KeyError, IndexError):
                # Input not available on this switcher model
                inputs.append({
                    "source": i,
                    "long_name": "",
                    "short_name": "",
                })

        return inputs

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return connection status summary."""
        return {
            "connected": self.connected,
            "atem_ip": self._config.atem_ip,
            "enabled": self._config.enabled,
        }