"""Bridge core.

Contains the asyncio UDP relay logic, route table, sequence mapping,
per-route controller session tracking, diagnostics tracking, and command dispatch.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

from .config import BridgeConfig, CameraRoute
from .protocols import get_input_profile, get_output_profile
from .protocols.base import InputProfile, OutputProfile
from .protocols.visca import build_visca_ip_packet, parse_visca_ip_packet, VISCA_REPLY_TYPE

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CommandResult:
    """Result of a command or test operation."""

    ok: bool
    result: str
    detail: str = ""


class _UDPEndpoint:
    """Async UDP endpoint that can send and receive."""

    def __init__(self, transport: asyncio.DatagramTransport, protocol: "_UDPProtocol") -> None:
        self.transport = transport
        self.protocol = protocol

    def send(self, data: bytes, addr: tuple[str, int]) -> None:
        self.transport.sendto(data, addr)

    async def receive(self) -> tuple[bytes, tuple[str, int]]:
        return await self.protocol.queue.get()

    def close(self) -> None:
        self.transport.close()


class _UDPProtocol(asyncio.DatagramProtocol):
    """Simple datagram protocol that pushes packets into an asyncio queue."""

    def __init__(self, queue: asyncio.Queue[tuple[bytes, tuple[str, int]]]) -> None:
        self.queue = queue

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self.queue.put_nowait((data, addr))
        except asyncio.QueueFull:
            logger.warning("UDP queue full, dropping packet from %s", addr)

    def error_received(self, exc: Exception | None) -> None:
        logger.warning("UDP error received: %s", exc)


async def _bind_udp_endpoint(host: str, port: int) -> _UDPEndpoint:
    """Bind a UDP socket and return an endpoint wrapper."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue(maxsize=256)
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: _UDPProtocol(queue),
        local_addr=(host, port),
    )
    return _UDPEndpoint(transport, protocol)


class BridgeCore:
    """Bridge core with async UDP relay, diagnostics, command dispatch, and per-route state."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._route_states: dict[int, dict[str, Any]] = {}
        self._diag: dict[str, Any] = {
            "last_controller_addr": None,
            "last_command": None,
            "last_camera_reply": None,
            "event_log": [],
            "command_count": 0,
            "reply_count": 0,
            "error_count": 0,
        }
        # Per-route controller session tracking: maps camera-side seq -> controller-side seq + return addr
        self._pending_replies: dict[int, dict[int, tuple[int, tuple[str, int], str]]] = {}
        # Async resources
        self._listeners: list[_UDPEndpoint] = []
        self._camera_sockets: dict[int, _UDPEndpoint] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False

    # ------------------------------------------------------------------
    # Route state
    # ------------------------------------------------------------------

    def route_state(self, route_index: int) -> dict[str, Any]:
        """Return (and create if needed) per-route mutable state."""
        if route_index not in self._route_states:
            self._route_states[route_index] = {}
        return self._route_states[route_index]

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        """Return current diagnostics snapshot."""
        routes = [
            {
                "index": i,
                "label": r.label,
                "enabled": r.enabled,
                "status": r.status,
            }
            for i, r in enumerate(self.config.routes)
        ]
        return {
            **self._diag,
            "routes": routes,
        }

    def reset_diagnostics(self) -> None:
        """Reset diagnostics counters and per-route state."""
        self._diag = {
            "last_controller_addr": None,
            "last_command": None,
            "last_camera_reply": None,
            "event_log": [],
            "command_count": 0,
            "reply_count": 0,
            "error_count": 0,
        }
        self._route_states.clear()
        self._pending_replies.clear()

    def _log_event(self, message: str) -> None:
        """Append an event to the diagnostics log (capped at 200 lines)."""
        log: list[str] = self._diag["event_log"]
        log.append(message)
        if len(log) > 200:
            self._diag["event_log"] = log[-200:]

    # ------------------------------------------------------------------
    # Profile resolution
    # ------------------------------------------------------------------

    def _resolve_profiles(self, route: CameraRoute) -> tuple[InputProfile, OutputProfile] | None:
        try:
            input_cls = get_input_profile(route.input_profile)
            output_cls = get_output_profile(route.output_profile)
            return input_cls(), output_cls()
        except KeyError as exc:
            logger.error("Failed to resolve profile for route %s: %s", route.label, exc)
            return None

    # ------------------------------------------------------------------
    # Async lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start UDP listeners for all enabled routes and camera reply handlers."""
        if self._running:
            return
        self._running = True

        for idx, route in self.config.enabled_routes():
            profiles = self._resolve_profiles(route)
            if profiles is None:
                route.status = "error"
                continue
            input_prof, output_prof = profiles

            # For Sony BRBK-IP10, the camera socket MUST bind to port 52381
            # (the camera replies back to source port 52381).
            # The listener for controller packets should use a DIFFERENT port
            # (the incoming_port from config, e.g. 52381 for camera 1).
            # If incoming_port == source_port (both 52381), we have a conflict.
            # Solution: use a single socket per route that handles both directions,
            # filtering by source address (controller vs camera).

            source_port = output_prof.source_port()
            bind_ip = self.config.bridge_ip or "0.0.0.0"

            if source_port and source_port == route.incoming_port:
                # Single-socket mode: one socket handles both controller and camera traffic
                try:
                    sock = await _bind_udp_endpoint(bind_ip, source_port)
                    self._camera_sockets[idx] = sock
                    self._listeners.append(sock)  # also add as listener for reply sending
                    logger.info("Route %s single-socket mode on %s:%d (controller+camera)",
                                route.label, bind_ip, source_port)
                except OSError as exc:
                    logger.error("Failed to bind socket for route %s: %s", route.label, exc)
                    route.status = "error"
                    continue

                # Send camera reset on startup to clear sequence state
                await self._send_camera_reset(idx, route, output_prof, sock)

                # Single task handles both directions
                self._tasks.append(
                    asyncio.create_task(
                        self._unified_socket_task(idx, route, input_prof, output_prof, sock),
                        name=f"unified_{idx}",
                    )
                )
            else:
                # Two-socket mode: separate listener and camera socket
                try:
                    listener = await _bind_udp_endpoint(self.config.controller_bind_ip, route.incoming_port)
                    self._listeners.append(listener)
                    logger.info("Route %s listening on %s:%d", route.label, self.config.controller_bind_ip, route.incoming_port)
                except OSError as exc:
                    logger.error("Failed to bind listener for route %s: %s", route.label, exc)
                    route.status = "error"
                    continue

                try:
                    if source_port:
                        camera_sock = await _bind_udp_endpoint(bind_ip, source_port)
                    else:
                        camera_sock = await _bind_udp_endpoint(bind_ip, 0)
                    self._camera_sockets[idx] = camera_sock
                    logger.info("Route %s camera socket bound to source port %d", route.label, source_port or 0)
                except OSError as exc:
                    logger.error("Failed to bind camera socket for route %s: %s", route.label, exc)
                    route.status = "error"
                    continue

                await self._send_camera_reset(idx, route, output_prof, self._camera_sockets[idx])

                self._tasks.append(
                    asyncio.create_task(
                        self._controller_listener_task(idx, route, input_prof, output_prof, listener),
                        name=f"controller_listener_{idx}",
                    )
                )
                self._tasks.append(
                    asyncio.create_task(
                        self._camera_reply_task(idx, route, input_prof, output_prof, self._camera_sockets[idx]),
                        name=f"camera_reply_{idx}",
                    )
                )

            route.status = "ok"

    async def stop(self) -> None:
        """Stop all listeners and relay tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for listener in self._listeners:
            listener.close()
        self._listeners.clear()
        for sock in self._camera_sockets.values():
            sock.close()
        self._camera_sockets.clear()

    # ------------------------------------------------------------------
    # Camera reset
    # ------------------------------------------------------------------

    async def _send_camera_reset(self, idx: int, route: CameraRoute,
                                  output_prof: OutputProfile, sock: _UDPEndpoint) -> None:
        """Send a Sony CONTROL reset to clear camera sequence state on startup."""
        import struct
        # Sony control reset: type 0x0200, payload 0x01, seq 0
        reset_pkt = struct.pack(">HHI", 0x0200, 1, 0) + bytes([0x01])
        try:
            sock.send(reset_pkt, (route.camera_ip, route.camera_port))
            self._log_event(f"[{route.label}] Camera reset sent")
            # Drain immediate replies
            import asyncio as _aio
            try:
                while True:
                    await _aio.wait_for(sock.receive(), timeout=0.25)
            except _aio.TimeoutError:
                pass
        except Exception as exc:
            logger.warning("Camera reset failed for route %s: %s", route.label, exc)
        # Reset sequence state
        route_state = self.route_state(idx)
        route_state["sony_seq"] = 0
        pending = self._pending_replies.get(idx, {})
        pending.clear()

    # ------------------------------------------------------------------
    # Unified socket task (single-socket mode for port-shared routes)
    # ------------------------------------------------------------------

    async def _unified_socket_task(
        self,
        idx: int,
        route: CameraRoute,
        input_prof: InputProfile,
        output_prof: OutputProfile,
        sock: _UDPEndpoint,
    ) -> None:
        """Handle both controller and camera traffic on a single socket.

        Distinguishes by source address: if from camera_ip → it's a camera reply;
        otherwise → it's a controller command.
        """
        while self._running:
            try:
                data, addr = await sock.receive()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Unified socket error on route %s: %s", route.label, exc)
                self._diag["error_count"] += 1
                continue

            is_camera_reply = addr[0] == route.camera_ip

            if is_camera_reply:
                await self._handle_camera_reply(idx, route, input_prof, output_prof, data, addr, sock)
            else:
                await self._handle_controller_command(idx, route, input_prof, output_prof, data, addr, sock)

    async def _handle_controller_command(
        self,
        idx: int,
        route: CameraRoute,
        input_prof: InputProfile,
        output_prof: OutputProfile,
        data: bytes,
        addr: tuple[str, int],
        sock: _UDPEndpoint,
    ) -> None:
        """Process a packet from the controller and forward to camera."""
        self._diag["last_controller_addr"] = addr
        self._diag["command_count"] += 1
        self._log_event(f"[{route.label}] RX from controller {addr}: {data.hex()}")

        decoded = input_prof.decode(data, addr)
        if decoded is None:
            self._diag["error_count"] += 1
            self._log_event(f"[{route.label}] Unrecognised packet from {addr}")
            return

        route_state = self.route_state(idx)
        camera_packet = output_prof.encode(decoded, route_state)
        if camera_packet is None:
            self._diag["error_count"] += 1
            self._log_event(f"[{route.label}] Encode failed for command {decoded.get('type')}")
            return

        camera_seq = self._extract_seq(camera_packet)
        controller_seq = decoded.get("seq", 0)
        framing = decoded.get("framing", "visca_ip")
        if camera_seq is not None:
            pending = self._pending_replies.setdefault(idx, {})
            pending[camera_seq] = (controller_seq, addr, framing)

        self._diag["last_command"] = {
            "route_index": idx,
            "command": decoded.get("type"),
            "payload_hex": decoded.get("payload", b"").hex(),
            "camera_seq": camera_seq,
        }
        self._log_event(f"[{route.label}] TX to camera: {camera_packet.hex()}")
        sock.send(camera_packet, (route.camera_ip, route.camera_port))

    async def _handle_camera_reply(
        self,
        idx: int,
        route: CameraRoute,
        input_prof: InputProfile,
        output_prof: OutputProfile,
        data: bytes,
        addr: tuple[str, int],
        sock: _UDPEndpoint,
    ) -> None:
        """Process a reply from the camera and forward to controller."""
        self._diag["reply_count"] += 1
        self._log_event(f"[{route.label}] Camera reply from {addr}: {data.hex()}")

        # Parse the Sony reply packet directly (handles all reply types)
        parsed = parse_visca_ip_packet(data)
        if parsed is None:
            # Not a standard VISCA-IP packet — try raw
            self._diag["error_count"] += 1
            self._log_event(f"[{route.label}] Unrecognised camera reply from {addr}")
            return

        payload_type, payload_length, cam_seq, payload = parsed

        # Check for sequence abnormality error
        if payload_type == 0x0200 and payload == bytes([0x0F, 0x01]):
            self._log_event(f"[{route.label}] Camera sequence error — resetting")
            # Reset sequence state
            route_state = self.route_state(idx)
            route_state["sony_seq"] = 0
            # Send reset
            import struct
            reset_pkt = struct.pack(">HHI", 0x0200, 1, 0) + bytes([0x01])
            sock.send(reset_pkt, (route.camera_ip, route.camera_port))
            # Drain
            import asyncio as _aio
            try:
                while True:
                    await _aio.wait_for(sock.receive(), timeout=0.25)
            except _aio.TimeoutError:
                pass
            route_state["sony_seq"] = 0
            self._pending_replies.get(idx, {}).clear()
            return

        # Find the pending reply mapping
        pending = self._pending_replies.get(idx, {})
        mapped = pending.pop(cam_seq, None) if cam_seq is not None else None

        if mapped is not None:
            controller_seq, return_addr, framing = mapped
            # Build reply for controller
            reply_data = input_prof.encode_reply(
                {"payload": payload, "payload_type": payload_type},
                {"seq": controller_seq, "framing": framing, "payload_type": 0x0111},
            )
            if reply_data is not None:
                sock.send(reply_data, return_addr)
                self._log_event(f"[{route.label}] Reply to controller {return_addr}: {reply_data.hex()}")
        else:
            # No mapping found — send to last known controller if any
            if self._diag.get("last_controller_addr"):
                return_addr = self._diag["last_controller_addr"]
                # Try to build a reply with the raw data
                if framing := "visca_ip":
                    reply_data = input_prof.encode_reply(
                        {"payload": payload, "payload_type": payload_type},
                        {"seq": 1, "framing": "visca_ip", "payload_type": payload_type},
                    )
                    if reply_data is not None:
                        sock.send(reply_data, return_addr)
                        self._log_event(f"[{route.label}] Reply (unmapped) to controller {return_addr}: {reply_data.hex()}")

        self._diag["last_camera_reply"] = {
            "route_index": idx,
            "payload_hex": payload.hex(),
            "camera_seq": cam_seq,
        }

    # ------------------------------------------------------------------
    # Relay tasks (two-socket mode)
    # ------------------------------------------------------------------

    async def _controller_listener_task(
        self,
        idx: int,
        route: CameraRoute,
        input_prof: InputProfile,
        output_prof: OutputProfile,
        listener: _UDPEndpoint,
    ) -> None:
        """Listen for controller packets, decode them, rewrite, and forward to camera."""
        while self._running:
            try:
                data, addr = await listener.receive()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Controller listener error on route %s: %s", route.label, exc)
                self._diag["error_count"] += 1
                continue

            self._diag["last_controller_addr"] = addr
            self._diag["command_count"] += 1
            self._log_event(f"[{route.label}] RX from {addr}: {data.hex()}")

            decoded = input_prof.decode(data, addr)
            if decoded is None:
                self._diag["error_count"] += 1
                self._log_event(f"[{route.label}] Unrecognised packet from {addr}")
                continue

            # Get or create per-route state
            route_state = self.route_state(idx)

            # Encode for camera output profile (forces address, rewrites sequence)
            camera_packet = output_prof.encode(decoded, route_state)
            if camera_packet is None:
                self._diag["error_count"] += 1
                self._log_event(f"[{route.label}] Encode failed for command {decoded.get('type')}")
                continue

            # Track pending reply mapping: camera_seq -> (controller_seq, controller_addr, framing)
            camera_seq = self._extract_seq(camera_packet)
            controller_seq = decoded.get("seq", 0)
            framing = decoded.get("framing", "visca_ip")
            if camera_seq is not None:
                pending = self._pending_replies.setdefault(idx, {})
                pending[camera_seq] = (controller_seq, addr, framing)

            self._diag["last_command"] = {
                "route_index": idx,
                "command": decoded.get("type"),
                "payload_hex": decoded.get("payload", b"").hex(),
                "camera_seq": camera_seq,
            }
            self._log_event(f"[{route.label}] TX to camera: {camera_packet.hex()}")

            # Send to camera
            camera_sock = self._camera_sockets.get(idx)
            if camera_sock is not None:
                camera_sock.send(camera_packet, (route.camera_ip, route.camera_port))

    async def _camera_reply_task(
        self,
        idx: int,
        route: CameraRoute,
        input_prof: InputProfile,
        output_prof: OutputProfile,
        camera_sock: _UDPEndpoint,
    ) -> None:
        """Listen for camera replies, decode them, map sequence back, and return to controller."""
        while self._running:
            try:
                data, addr = await camera_sock.receive()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Camera reply error on route %s: %s", route.label, exc)
                self._diag["error_count"] += 1
                continue

            self._diag["reply_count"] += 1
            self._log_event(f"[{route.label}] Camera reply from {addr}: {data.hex()}")

            route_state = self.route_state(idx)
            decoded = output_prof.decode_reply(data, route_state)
            if decoded is None:
                self._diag["error_count"] += 1
                self._log_event(f"[{route.label}] Unrecognised camera reply from {addr}")
                continue

            camera_seq = decoded.get("seq")
            pending = self._pending_replies.get(idx, {})
            mapped = pending.pop(camera_seq, None) if camera_seq is not None else None

            if mapped is not None:
                controller_seq, return_addr, framing = mapped
                # Build reply for controller with original sequence number and framing
                reply_cmd = {
                    "seq": controller_seq,
                    "framing": framing,
                    "payload_type": decoded.get("payload_type", VISCA_REPLY_TYPE),
                }
                reply_data = input_prof.encode_reply(
                    {"payload": decoded.get("payload"), "payload_type": decoded.get("payload_type", VISCA_REPLY_TYPE)},
                    reply_cmd,
                )
                if reply_data is not None:
                    # Send back to controller via the listener socket (same local port)
                    listener = self._listeners[idx] if idx < len(self._listeners) else None
                    if listener is not None:
                        listener.send(reply_data, return_addr)
                        self._log_event(f"[{route.label}] Reply to controller {return_addr}: {reply_data.hex()}")

            self._diag["last_camera_reply"] = {
                "route_index": idx,
                "payload_hex": decoded.get("payload", b"").hex(),
                "camera_seq": camera_seq,
            }

    @staticmethod
    def _extract_seq(packet: bytes) -> int | None:
        """Extract the sequence number from a VISCA-over-IP packet."""
        parsed = parse_visca_ip_packet(packet)
        if parsed is not None:
            return parsed[2]
        return None

    # ------------------------------------------------------------------
    # Command dispatch (manual control via API/UI)
    # ------------------------------------------------------------------

    async def send_command(self, route_index: int, command: str, args: dict[str, Any]) -> CommandResult:
        """Dispatch a manual control command to a camera route.

        Builds the correct VISCA payload, sends it via the camera socket,
        and returns what was sent. If the bridge is not running, falls back
        to building the payload without transmitting.
        """
        if not (0 <= route_index < len(self.config.routes)):
            return CommandResult(ok=False, result="error", detail="route index out of range")
        route = self.config.routes[route_index]
        if not route.enabled:
            return CommandResult(ok=False, result="error", detail="route is disabled")

        # Resolve profiles early so we can check for OSD payload overrides
        profiles = self._resolve_profiles(route)
        output_prof = profiles[1] if profiles else None

        # If the output profile overrides this OSD command, use its payload directly.
        # Otherwise fall back to the bridge core's default payload builder.
        if output_prof is not None and command in output_prof.OSD_PAYLOADS:
            payload = output_prof.OSD_PAYLOADS[command]
        else:
            payload = self._build_visca_payload(command, args)
        if payload is None:
            return CommandResult(ok=False, result="error", detail=f"unknown command: {command}")

        self._diag["last_command"] = {
            "route_index": route_index,
            "command": command,
            "args": args,
            "payload_hex": payload.hex(),
        }
        self._diag["command_count"] += 1
        self._log_event(f"[{route.label}] {command}")

        if self._running:
            if profiles is not None:
                input_prof, output_prof_live = profiles
                route_state = self.route_state(route_index)
                # Build a synthetic decoded command for the output profile
                cmd = {
                    "payload": payload,
                    "payload_type": 0x0200,  # VISCA_COMMAND_TYPE
                    "seq": 0,
                    "framing": "visca_ip",
                }
                camera_packet = output_prof_live.encode(cmd, route_state)
                if camera_packet is not None:
                    camera_sock = self._camera_sockets.get(route_index)
                    if camera_sock is not None:
                        camera_sock.send(camera_packet, (route.camera_ip, route.camera_port))
                        return CommandResult(ok=True, result="sent", detail=f"payload {payload.hex()}")

        return CommandResult(ok=True, result="built", detail=f"payload {payload.hex()}")

    def _build_visca_payload(self, command: str, args: dict[str, Any]) -> bytes | None:
        """Build a VISCA payload bytes for a given command.

        Returns None if the command is not recognised.
        Address byte is omitted here — the output profile forces it to 0x81.
        """
        # Pan/tilt commands (VISCA 8x 01 06 01 VV WW 03 03 FF)
        # VV = pan speed, WW = tilt speed
        pan_speed = args.get("pan_speed", 3)
        tilt_speed = args.get("tilt_speed", 3)

        match command:
            case "pan_left":
                return bytes([0x01, 0x06, 0x01, pan_speed, tilt_speed, 0x01, 0x03, 0xFF])
            case "pan_right":
                return bytes([0x01, 0x06, 0x01, pan_speed, tilt_speed, 0x02, 0x03, 0xFF])
            case "tilt_up":
                return bytes([0x01, 0x06, 0x01, pan_speed, tilt_speed, 0x03, 0x01, 0xFF])
            case "tilt_down":
                return bytes([0x01, 0x06, 0x01, pan_speed, tilt_speed, 0x03, 0x02, 0xFF])
            case "stop":
                return bytes([0x01, 0x06, 0x01, 0x01, 0x01, 0x03, 0x03, 0xFF])
            case "zoom_in":
                return bytes([0x01, 0x04, 0x07, 0x02, 0xFF])
            case "zoom_out":
                return bytes([0x01, 0x04, 0x07, 0x03, 0xFF])
            case "focus_near":
                return bytes([0x01, 0x04, 0x08, 0x02, 0xFF])
            case "focus_far":
                return bytes([0x01, 0x04, 0x08, 0x03, 0xFF])
            case "autofocus_toggle":
                return bytes([0x01, 0x04, 0x38, 0x02, 0xFF])
            case "preset_save":
                preset = args.get("preset", 1)
                if not (1 <= preset <= 16):
                    return None
                return bytes([0x01, 0x04, 0x3F, 0x01, preset, 0xFF])
            case "preset_recall":
                preset = args.get("preset", 1)
                if not (1 <= preset <= 16):
                    return None
                return bytes([0x01, 0x04, 0x3F, 0x02, preset, 0xFF])
            case "menu_open":
                return bytes([0x01, 0x06, 0x06, 0x02, 0xFF])
            case "menu_close":
                return bytes([0x01, 0x06, 0x06, 0x03, 0xFF])
            case "menu_enter":
                return bytes([0x01, 0x06, 0x06, 0x05, 0xFF])
            case "menu_back":
                return bytes([0x01, 0x06, 0x06, 0x04, 0xFF])
            case _:
                return None

    # ------------------------------------------------------------------
    # Test commands
    # ------------------------------------------------------------------

    async def test_route(self, route_index: int, test_type: str) -> CommandResult:
        """Run a test against a camera route.

        Types:
          - "ping": basic connectivity check (placeholder)
          - "version": send VISCA version inquiry
          - "stop": send pan/tilt stop
        """
        if not (0 <= route_index < len(self.config.routes)):
            return CommandResult(ok=False, result="error", detail="route index out of range")
        route = self.config.routes[route_index]
        if not route.enabled:
            return CommandResult(ok=False, result="error", detail="route is disabled")

        match test_type:
            case "ping":
                # Placeholder: real implementation would try UDP reachability
                return CommandResult(ok=True, result="ok", detail=f"ping placeholder for {route.camera_ip}:{route.camera_port}")
            case "version":
                # VISCA version inquiry: 81 09 00 02 FF
                payload = bytes([0x09, 0x00, 0x02, 0xFF])
                self._diag["last_command"] = {
                    "route_index": route_index,
                    "command": "version_inquiry",
                    "payload_hex": payload.hex(),
                }
                self._log_event(f"[{route.label}] version inquiry")
                # If running, send via bridge; otherwise just build
                if self._running:
                    result = await self.send_command(route_index, "version_inquiry", {})
                    return result
                return CommandResult(ok=True, result="ok", detail=f"version inquiry built for {route.camera_ip}:{route.camera_port}")
            case "stop":
                payload = bytes([0x01, 0x06, 0x01, 0x01, 0x01, 0x03, 0x03, 0xFF])
                self._diag["last_command"] = {
                    "route_index": route_index,
                    "command": "stop",
                    "payload_hex": payload.hex(),
                }
                self._log_event(f"[{route.label}] stop test")
                if self._running:
                    result = await self.send_command(route_index, "stop", {})
                    return result
                return CommandResult(ok=True, result="ok", detail=f"stop built for {route.camera_ip}:{route.camera_port}")
            case _:
                return CommandResult(ok=False, result="error", detail=f"unknown test type: {test_type}")
