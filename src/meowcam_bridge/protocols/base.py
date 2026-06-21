"""Base protocol interfaces.

The bridge core interacts with controllers and cameras only through these abstractions.
Concrete profiles live in input_*.py and output_*.py modules.
"""

from __future__ import annotations

import abc
from typing import Any


class InputProfile(abc.ABC):
    """Abstract interface for controller-side protocol handlers.

    Responsible for decoding incoming controller packets into generic command
    representations and encoding replies back to controller format.
    """

    name: str = ""
    description: str = ""

    @abc.abstractmethod
    def decode(self, data: bytes, source_addr: tuple[str, int]) -> dict[str, Any] | None:
        """Decode a raw UDP packet from the controller.

        Returns a dict with at least:
          - "type": command type string (e.g. "pan_tilt", "zoom", "preset_recall")
          - "payload": command-specific data
          - "seq": optional sequence number from the controller
        Returns None if the packet is not recognised or is malformed.
        """
        ...  # pragma: no cover

    @abc.abstractmethod
    def encode_reply(self, reply: dict[str, Any], original_cmd: dict[str, Any]) -> bytes | None:
        """Encode a reply from the camera/bridge back into controller format.

        ``reply`` contains at least:
          - "type": reply type (e.g. "ack", "completion", "inquiry_response")
          - "payload": reply-specific data
        ``original_cmd`` is the decoded command dict returned by decode().
        Returns None if no reply should be sent.
        """
        ...  # pragma: no cover

    @abc.abstractmethod
    def supports(self, capability: str) -> bool:
        """Return True if this input profile supports a given capability.

        Capabilities: "pan_tilt", "zoom", "focus", "preset_recall", "preset_save",
        "autofocus", "menu_osd", "inquiry".
        """
        ...  # pragma: no cover


class OutputProfile(abc.ABC):
    """Abstract interface for camera-side protocol handlers.

    Responsible for translating generic bridge commands into camera-specific packets
    and rewriting/normalising camera replies for the bridge.
    """

    name: str = ""
    description: str = ""

    @abc.abstractmethod
    def encode(self, cmd: dict[str, Any], route_state: dict[str, Any]) -> bytes | None:
        """Encode a generic bridge command into camera wire format.

        ``route_state`` is a per-route mutable dict managed by the bridge core
        (e.g. for sequence-number tracking). Profiles may read/write keys they
        document, but must not assume keys from other profiles exist.
        Returns None if the command is unsupported by this camera profile.
        """
        ...  # pragma: no cover

    @abc.abstractmethod
    def decode_reply(self, data: bytes, route_state: dict[str, Any]) -> dict[str, Any] | None:
        """Decode a raw reply from the camera into a generic reply dict.

        Returns a dict with at least:
          - "type": reply type string
          - "payload": reply-specific data
          - "seq": optional camera-side sequence number
        Returns None if the packet is unrecognised or malformed.
        """
        ...  # pragma: no cover

    @abc.abstractmethod
    def source_port(self) -> int:
        """Return the fixed UDP source port this camera profile requires for outbound packets.

        For Sony BRBK-IP10 this is 52381. For generic VISCA/IP cameras this may
        be 0 (ephemeral) if the camera does not enforce a specific reply port.
        """
        ...  # pragma: no cover

    @abc.abstractmethod
    def supports(self, capability: str) -> bool:
        """Return True if this output profile supports a given capability."""
        ...  # pragma: no cover
