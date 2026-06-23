"""Bridge core.

Contains the asyncio UDP relay logic, route table, sequence mapping,
per-route controller session tracking, diagnostics tracking, and command dispatch.

Supports multi-camera operation with shared camera sockets: when multiple routes
use the same camera source port (e.g. Sony BRBK-IP10 cameras all need port 52381),
a single shared socket handles all cameras, routing replies by source IP.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import sys
from typing import Any

from .config import BridgeConfig, CameraRoute
from .protocols import get_input_profile, get_output_profile
from .protocols.base import InputProfile, OutputProfile
from .protocols.visca import build_visca_ip_packet, parse_visca_ip_packet, VISCA_REPLY_TYPE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows UDP connreset fix
# ---------------------------------------------------------------------------
# On Windows, sending UDP packets to an unreachable host (e.g. a powered-off
# camera) causes the OS to return ICMP "port unreachable", which Windows
# surfaces as WSAECONNRESET (WinError 10054) on the next socket operation.
# Python's ProactorEventLoop turns this into ConnectionResetError exceptions
# that flood the event loop and block the web UI for ~30 seconds.

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import socket as _socket

    def _disable_connreset(sock: "_socket.socket") -> None:
        """Disable WSAECONNRESET on a Windows UDP socket."""
        try:
            sock.ioctl(_socket.SIO_UDP_CONNRESET, 0)
        except (AttributeError, OSError) as exc:
            try:
                import ctypes as _ctypes
                import ctypes.wintypes as _wt
                SIO_UDP_CONNRESET = 0x9800000C
                _WSAIoctl = _ctypes.windll.ws2_32.WSAIoctl
                _WSAIoctl.argtypes = [
                    _wt.HANDLE, _wt.DWORD, _ctypes.c_void_p, _wt.DWORD,
                    _ctypes.c_void_p, _wt.DWORD, _ctypes.POINTER(_wt.DWORD),
                    _ctypes.c_void_p, _ctypes.c_void_p,
                ]
                _WSAIoctl.restype = _wt.BOOL
                sock_handle = _ctypes.c_void_p(sock.fileno())
                opt = _ctypes.c_ulong(0)
                bytes_returned = _wt.DWORD(0)
                result = _WSAIoctl(
                    sock_handle, SIO_UDP_CONNRESET,
                    _ctypes.pointer(opt), _ctypes.sizeof(opt),
                    None, 0, _ctypes.pointer(bytes_returned),
                    None, None,
                )
                if result == 0:
                    logger.info("SIO_UDP_CONNRESET disabled via WSAIoctl fallback")
                else:
                    logger.warning("WSAIoctl SIO_UDP_CONNRESET failed: result=%d", result)
            except Exception as exc2:
                logger.warning("Failed to disable SIO_UDP_CONNRESET (ioctl: %s, fallback: %s)", exc, exc2)
else:
    def _disable_connreset(sock: Any) -> None:
        pass


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
        try:
            self.transport.sendto(data, addr)
        except (ConnectionResetError, ConnectionRefusedError):
            pass

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
        if isinstance(exc, ConnectionResetError):
            return
        logger.warning("UDP error received: %s", exc)


async def _bind_udp_endpoint(host: str, port: int) -> _UDPEndpoint:
    """Bind a UDP socket and return an endpoint wrapper."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[bytes, tuple[str, int]]] = asyncio.Queue(maxsize=256)
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: _UDPProtocol(queue),
        local_addr=(host, port),
    )
    # Windows: disable WSAECONNRESET on the underlying socket
    sock = transport.get_extra_info("socket")
    if sock is not None:
        _disable_connreset(sock)
    return _UDPEndpoint(transport, protocol)


class BridgeCore:
    """Bridge core with async UDP relay, diagnostics, command dispatch, and per-route state.

    Socket architecture:
      - Single-socket mode: when incoming_port == source_port, one socket handles
        both controller commands and camera replies for that route.
      - Two-socket mode (shared): when multiple routes share the same source_port
        (e.g. multiple Sony cameras all needing port 52381), a single shared camera
        socket is created per source_port. Controller listeners are per-route.
        Camera replies are routed by source IP address.
      - Two-socket mode (solo): legacy fallback for routes with unique source_ports.
    """

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
        # Per-route camera-side sequences generated internally by the bridge
        # (for example preset-speed helper commands). Replies for these should
        # be consumed by the bridge, not forwarded to the hardware controller.
        self._internal_replies: dict[int, set[int]] = {}
        # Async resources
        self._listeners: list[_UDPEndpoint] = []
        self._listeners_by_route: dict[int, _UDPEndpoint] = {}
        self._camera_sockets: dict[int, _UDPEndpoint] = {}  # per-route (single-socket mode)
        self._shared_camera_sockets: dict[int, _UDPEndpoint] = {}  # per source_port (shared mode)
        self._camera_ip_to_route: dict[str, int] = {}  # reverse map for reply routing
        self._route_output_profs: dict[int, OutputProfile] = {}  # cached output profiles
        self._route_input_profs: dict[int, InputProfile] = {}  # cached input profiles
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False
        # Per-route command locks: prevent concurrent VISCA commands to the
        # same camera (avoids sequence-number collisions and crash cascades).
        # Each route gets its own lock, so Camera 2 can receive commands
        # while Camera 1 is still moving.
        self._route_locks: dict[int, asyncio.Lock] = {}
        self._route_busy: dict[int, float] = {}  # route_index -> monotonic timestamp
        self._lock_timeout = 15.0  # auto-release lock after 15s

    # ------------------------------------------------------------------
    # Route state
    # ------------------------------------------------------------------

    def route_state(self, route_index: int) -> dict[str, Any]:
        """Return (and create if needed) per-route mutable state."""
        if route_index not in self._route_states:
            self._route_states[route_index] = {}
        return self._route_states[route_index]

    def _get_route_lock(self, route_index: int) -> asyncio.Lock:
        """Return (and create if needed) the per-route command lock."""
        if route_index not in self._route_locks:
            self._route_locks[route_index] = asyncio.Lock()
        return self._route_locks[route_index]

    def is_route_busy(self, route_index: int) -> bool:
        """Check if a route is currently processing a command."""
        import time
        ts = self._route_busy.get(route_index)
        if ts is None:
            return False
        # Auto-expire stale busy state after timeout
        if time.monotonic() - ts > self._lock_timeout:
            self._route_busy.pop(route_index, None)
            return False
        return True

    def _release_busy(self, route_index: int) -> None:
        """Mark a route as no longer busy."""
        self._route_busy.pop(route_index, None)

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
        # Do not clear route_state/sony_seq here while the bridge is live.
        # The BRBK-IP10 keeps its own sequence counter; clearing only our side
        # without also sending a camera reset causes sequence-abnormality errors
        # on the next command. A bridge restart performs a real camera reset.
        self._pending_replies.clear()
        self._internal_replies.clear()

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

        enabled = list(self.config.enabled_routes())
        if not enabled:
            return

        bind_ip = self.config.bridge_ip or "0.0.0.0"

        # First pass: separate single-socket routes from two-socket routes
        single_socket_routes: list[tuple[int, CameraRoute, InputProfile, OutputProfile]] = []
        two_socket_routes: list[tuple[int, CameraRoute, InputProfile, OutputProfile]] = []

        for idx, route in enabled:
            profiles = self._resolve_profiles(route)
            if profiles is None:
                route.status = "error"
                continue
            input_prof, output_prof = profiles
            self._route_input_profs[idx] = input_prof
            self._route_output_profs[idx] = output_prof

            source_port = output_prof.source_port()
            if source_port and source_port == route.incoming_port:
                single_socket_routes.append((idx, route, input_prof, output_prof))
            else:
                two_socket_routes.append((idx, route, input_prof, output_prof))

        # --- Single-socket routes (unchanged behaviour) ---
        for idx, route, input_prof, output_prof in single_socket_routes:
            source_port = output_prof.source_port()
            try:
                sock = await _bind_udp_endpoint(bind_ip, source_port)
                self._camera_sockets[idx] = sock
                self._listeners.append(sock)
                logger.info("Route %s single-socket mode on %s:%d (controller+camera)",
                            route.label, bind_ip, source_port)
            except OSError as exc:
                logger.error("Failed to bind socket for route %s: %s", route.label, exc)
                route.status = "error"
                continue

            await self._send_camera_reset(idx, route, output_prof, sock)

            self._camera_ip_to_route[route.camera_ip] = idx

            self._tasks.append(
                asyncio.create_task(
                    self._unified_socket_task(idx, route, input_prof, output_prof, sock),
                    name=f"unified_{idx}",
                )
            )
            route.status = "ok"

        # --- Two-socket routes (shared camera socket per source_port) ---
        # Group by source_port
        port_groups: dict[int, list[tuple[int, CameraRoute, InputProfile, OutputProfile]]] = {}
        for idx, route, input_prof, output_prof in two_socket_routes:
            source_port = output_prof.source_port()
            port_groups.setdefault(source_port, []).append((idx, route, input_prof, output_prof))

        for source_port, group in port_groups.items():
            # Create ONE shared camera socket for this source_port
            shared_cam_sock: _UDPEndpoint | None = None
            if source_port:
                try:
                    shared_cam_sock = await _bind_udp_endpoint(bind_ip, source_port)
                    self._shared_camera_sockets[source_port] = shared_cam_sock
                    camera_count = len(group)
                    logger.info("Shared camera socket on %s:%d for %d camera(s)",
                                bind_ip, source_port, camera_count)
                except OSError as exc:
                    logger.error("Failed to bind shared camera socket on port %d: %s", source_port, exc)
                    for _, route, _, _ in group:
                        route.status = "error"
                    continue
            else:
                # No fixed source port — create per-route ephemeral sockets (legacy)
                pass

            # Process each route in this group
            for idx, route, input_prof, output_prof in group:
                # Create per-route controller listener
                try:
                    listener = await _bind_udp_endpoint(self.config.controller_bind_ip, route.incoming_port)
                    self._listeners.append(listener)
                    self._listeners_by_route[idx] = listener
                    logger.info("Route %s listening on %s:%d",
                                route.label, self.config.controller_bind_ip, route.incoming_port)
                except OSError as exc:
                    logger.error("Failed to bind listener for route %s: %s", route.label, exc)
                    route.status = "error"
                    continue

                # Register camera IP for reply routing
                self._camera_ip_to_route[route.camera_ip] = idx

                # Use shared socket if available, otherwise ephemeral per-route
                cam_sock = shared_cam_sock
                if cam_sock is None:
                    try:
                        cam_sock = await _bind_udp_endpoint(bind_ip, 0)
                    except OSError as exc:
                        logger.error("Failed to bind camera socket for route %s: %s", route.label, exc)
                        route.status = "error"
                        continue
                self._camera_sockets[idx] = cam_sock  # store for send_command access

                # Send camera reset
                await self._send_camera_reset(idx, route, output_prof, cam_sock)

                # Start controller listener (uses shared camera socket for sending)
                self._tasks.append(
                    asyncio.create_task(
                        self._controller_listener_task(idx, route, input_prof, output_prof, listener),
                        name=f"controller_listener_{idx}",
                    )
                )
                route.status = "ok"

            # Start ONE shared camera reply task for this source_port
            if shared_cam_sock is not None:
                self._tasks.append(
                    asyncio.create_task(
                        self._shared_camera_reply_task(source_port, shared_cam_sock, group),
                        name=f"shared_camera_reply_{source_port}",
                    )
                )

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
        self._listeners_by_route.clear()
        # Close per-route camera sockets (but not shared ones — they're separate)
        for idx, sock in list(self._camera_sockets.items()):
            # Don't double-close shared sockets
            if not any(sock is shared for shared in self._shared_camera_sockets.values()):
                sock.close()
        self._camera_sockets.clear()
        # Close shared camera sockets
        for sock in self._shared_camera_sockets.values():
            sock.close()
        self._shared_camera_sockets.clear()
        self._camera_ip_to_route.clear()
        self._route_output_profs.clear()
        self._route_input_profs.clear()

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

        Distinguishes by source address: if from camera_ip -> it's a camera reply;
        otherwise -> it's a controller command.
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
        asyncio.create_task(
            self._inject_preset_speed_if_needed(
                idx, route, output_prof, decoded.get("payload", b""), route_state, sock
            )
        )
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
            self._diag["error_count"] += 1
            self._log_event(f"[{route.label}] Unrecognised camera reply from {addr}")
            return

        payload_type, payload_length, cam_seq, payload = parsed

        # Check for sequence abnormality error
        if payload_type == 0x0200 and payload == bytes([0x0F, 0x01]):
            self._log_event(f"[{route.label}] Camera sequence error - resetting")
            route_state = self.route_state(idx)
            route_state["sony_seq"] = 0
            import struct
            reset_pkt = struct.pack(">HHI", 0x0200, 1, 0) + bytes([0x01])
            sock.send(reset_pkt, (route.camera_ip, route.camera_port))
            import asyncio as _aio
            try:
                while True:
                    await _aio.wait_for(sock.receive(), timeout=0.25)
            except _aio.TimeoutError:
                pass
            route_state["sony_seq"] = 0
            self._pending_replies.get(idx, {}).clear()
            self._release_busy(idx)
            return

        # Find the pending reply mapping.
        # Sony cameras send ACK then Completion with same seq. Internal helper
        # commands (e.g. preset-speed injection) should be consumed here rather
        # than forwarded to the hardware controller.
        pending = self._pending_replies.get(idx, {})
        is_ack = len(payload) >= 2 and payload[1] == 0x41
        is_completion_or_error = len(payload) >= 2 and (payload[1] == 0x51 or (payload[1] & 0x60) == 0x60)
        internal = self._internal_replies.get(idx, set())
        if cam_seq in internal:
            if is_completion_or_error:
                internal.discard(cam_seq)
            self._diag["last_camera_reply"] = {
                "route_index": idx,
                "payload_hex": payload.hex(),
                "camera_seq": cam_seq,
                "internal": True,
            }
            return
        if cam_seq is not None:
            mapped = pending.get(cam_seq) if is_ack else pending.pop(cam_seq, None)
        else:
            mapped = None

        if mapped is not None:
            controller_seq, return_addr, framing = mapped
            reply_data = input_prof.encode_reply(
                {"payload": payload, "payload_type": payload_type},
                {"seq": controller_seq, "framing": framing, "payload_type": 0x0111},
            )
            if reply_data is not None:
                sock.send(reply_data, return_addr)
                self._log_event(f"[{route.label}] Reply to controller {return_addr}: {reply_data.hex()}")
        else:
            # No mapping found - send to last known controller if any
            if self._diag.get("last_controller_addr"):
                return_addr = self._diag["last_controller_addr"]
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
        # Release busy state when command completes
        if is_completion_or_error:
            self._release_busy(idx)

    # ------------------------------------------------------------------
    # Controller listener task (two-socket mode)
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

            route_state = self.route_state(idx)
            camera_sock = self._camera_sockets.get(idx)
            asyncio.create_task(
                self._inject_preset_speed_if_needed(
                    idx, route, output_prof, decoded.get("payload", b""), route_state, camera_sock
                )
            )
            camera_packet = output_prof.encode(decoded, route_state)
            if camera_packet is None:
                self._diag["error_count"] += 1
                self._log_event(f"[{route.label}] Encode failed for command {decoded.get('type')}")
                continue

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

            # Send to camera via shared/per-route camera socket
            camera_sock = self._camera_sockets.get(idx)
            if camera_sock is not None:
                camera_sock.send(camera_packet, (route.camera_ip, route.camera_port))

    # ------------------------------------------------------------------
    # Shared camera reply task (handles multiple cameras on one socket)
    # ------------------------------------------------------------------

    async def _shared_camera_reply_task(
        self,
        source_port: int,
        sock: _UDPEndpoint,
        group: list[tuple[int, CameraRoute, InputProfile, OutputProfile]],
    ) -> None:
        """Listen for camera replies on a shared socket and route to the correct controller.

        Determines which route a reply belongs to by matching the source IP
        to camera IPs in the group. Each camera has its own sequence number space
        and pending reply map, so there is no cross-camera interference.
        """
        # Build IP -> route index map for this group
        ip_map = {route.camera_ip: idx for idx, route, _, _ in group}

        while self._running:
            try:
                data, addr = await sock.receive()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Shared camera reply error on port %d: %s", source_port, exc)
                self._diag["error_count"] += 1
                continue

            # Route by source IP
            camera_ip = addr[0]
            idx = ip_map.get(camera_ip)
            if idx is None:
                # Unknown camera — try global map
                idx = self._camera_ip_to_route.get(camera_ip)
            if idx is None:
                logger.warning("Reply from unknown camera %s on shared socket", camera_ip)
                self._diag["error_count"] += 1
                continue

            route = self.config.routes[idx]
            input_prof = self._route_input_profs.get(idx)
            output_prof = self._route_output_profs.get(idx)
            if input_prof is None or output_prof is None:
                profiles = self._resolve_profiles(route)
                if profiles is None:
                    continue
                input_prof, output_prof = profiles

            await self._handle_shared_camera_reply(idx, route, input_prof, output_prof,
                                                    data, addr, sock)

    async def _handle_shared_camera_reply(
        self,
        idx: int,
        route: CameraRoute,
        input_prof: InputProfile,
        output_prof: OutputProfile,
        data: bytes,
        addr: tuple[str, int],
        sock: _UDPEndpoint,
    ) -> None:
        """Process a camera reply from the shared socket and forward to controller."""
        self._diag["reply_count"] += 1
        self._log_event(f"[{route.label}] Camera reply from {addr}: {data.hex()}")

        parsed = parse_visca_ip_packet(data)
        if parsed is None:
            self._diag["error_count"] += 1
            self._log_event(f"[{route.label}] Unrecognised camera reply from {addr}")
            return

        payload_type, payload_length, cam_seq, payload = parsed

        # Check for sequence abnormality error
        if payload_type == 0x0200 and payload == bytes([0x0F, 0x01]):
            self._log_event(f"[{route.label}] Camera sequence error - resetting")
            route_state = self.route_state(idx)
            route_state["sony_seq"] = 0
            import struct
            reset_pkt = struct.pack(">HHI", 0x0200, 1, 0) + bytes([0x01])
            sock.send(reset_pkt, (route.camera_ip, route.camera_port))
            import asyncio as _aio
            try:
                while True:
                    await _aio.wait_for(sock.receive(), timeout=0.25)
            except _aio.TimeoutError:
                pass
            route_state["sony_seq"] = 0
            self._pending_replies.get(idx, {}).clear()
            self._release_busy(idx)
            return

        # ACK vs Completion handling. Internal helper commands (e.g.
        # preset-speed injection) should be consumed by the bridge and not
        # forwarded to the hardware controller.
        pending = self._pending_replies.get(idx, {})
        is_ack = len(payload) >= 2 and payload[1] == 0x41
        is_completion_or_error = len(payload) >= 2 and (payload[1] == 0x51 or (payload[1] & 0x60) == 0x60)
        internal = self._internal_replies.get(idx, set())
        if cam_seq in internal:
            if is_completion_or_error:
                internal.discard(cam_seq)
            self._diag["last_camera_reply"] = {
                "route_index": idx,
                "payload_hex": payload.hex(),
                "camera_seq": cam_seq,
                "internal": True,
            }
            return
        if cam_seq is not None:
            mapped = pending.get(cam_seq) if is_ack else pending.pop(cam_seq, None)
        else:
            mapped = None

        if mapped is not None:
            controller_seq, return_addr, framing = mapped
            reply_cmd = {
                "seq": controller_seq,
                "framing": framing,
                "payload_type": payload_type,
            }
            reply_data = input_prof.encode_reply(
                {"payload": payload, "payload_type": payload_type},
                reply_cmd,
            )
            if reply_data is not None:
                # Send via the route's listener socket (same port the controller sends to)
                listener = self._listeners_by_route.get(idx)
                if listener is not None:
                    listener.send(reply_data, return_addr)
                    self._log_event(f"[{route.label}] Reply to controller {return_addr}: {reply_data.hex()}")
                else:
                    # Fallback: send via camera socket (may not reach controller)
                    sock.send(reply_data, return_addr)
                    self._log_event(f"[{route.label}] Reply to controller (via cam sock) {return_addr}: {reply_data.hex()}")
        else:
            # No mapping found - send to last known controller if any
            if self._diag.get("last_controller_addr"):
                return_addr = self._diag["last_controller_addr"]
                reply_data = input_prof.encode_reply(
                    {"payload": payload, "payload_type": payload_type},
                    {"seq": 1, "framing": "visca_ip", "payload_type": payload_type},
                )
                if reply_data is not None:
                    listener = self._listeners_by_route.get(idx)
                    if listener is not None:
                        listener.send(reply_data, return_addr)
                        self._log_event(f"[{route.label}] Reply (unmapped) to controller {return_addr}: {reply_data.hex()}")

        self._diag["last_camera_reply"] = {
            "route_index": idx,
            "payload_hex": payload.hex(),
            "camera_seq": cam_seq,
        }
        # Release busy state when command completes
        if is_completion_or_error:
            self._release_busy(idx)

    @staticmethod
    def _extract_seq(packet: bytes) -> int | None:
        """Extract the sequence number from a VISCA-over-IP packet."""
        parsed = parse_visca_ip_packet(packet)
        if parsed is not None:
            return parsed[2]
        return None

    # ------------------------------------------------------------------
    # Preset speed helpers
    # ------------------------------------------------------------------

    SPEED_TO_PRESET_DRIVE: dict[str, int] = {
        "slow": 3,
        "medium": 9,
        "fast": 18,
    }

    @classmethod
    def _preset_drive_speed_for_mode(cls, mode: str) -> int:
        return cls.SPEED_TO_PRESET_DRIVE.get(mode, cls.SPEED_TO_PRESET_DRIVE["medium"])

    @staticmethod
    def _preset_number_from_recall_payload(payload: bytes) -> int | None:
        """Return 1-based preset number when payload is a VISCA preset recall."""
        if not payload:
            return None
        body = payload[1:] if 0x81 <= payload[0] <= 0x88 else payload
        if len(body) >= 6 and body[:4] == bytes([0x01, 0x04, 0x3F, 0x02]) and body[-1] == 0xFF:
            preset = int(body[4])
            if 1 <= preset <= 16:
                return preset
        return None

    @staticmethod
    def _build_preset_speed_payload(preset: int, speed: int) -> bytes | None:
        """Build BRC-H900 preset-drive-speed payload.

        Sony's BRC-H900 command list exposes PRESET DRIVE SPEED as:
        8x 01 7E 01 0B pp qq FF
        where pp = preset number - 1 and qq = direction speed (01-18).
        The output profile prepends/forces the 0x81 address byte, so this helper
        returns the address-less body.
        """
        if not (1 <= preset <= 16):
            return None
        speed = max(1, min(18, int(speed)))
        return bytes([0x01, 0x7E, 0x01, 0x0B, preset - 1, speed, 0xFF])

    async def _send_internal_payload(
        self,
        idx: int,
        route: CameraRoute,
        output_prof: OutputProfile,
        route_state: dict[str, Any],
        sock: _UDPEndpoint,
        payload: bytes,
        reason: str,
    ) -> None:
        cmd = {
            "payload": payload,
            "payload_type": 0x0100,
            "seq": 0,
            "framing": "visca_ip",
        }
        packet = output_prof.encode(cmd, route_state)
        if packet is None:
            return
        seq = self._extract_seq(packet)
        if seq is not None:
            self._internal_replies.setdefault(idx, set()).add(seq)
        self._log_event(f"[{route.label}] internal {reason}: {payload.hex()}")
        sock.send(packet, (route.camera_ip, route.camera_port))

    async def _inject_preset_speed_if_needed(
        self,
        idx: int,
        route: CameraRoute,
        output_prof: OutputProfile,
        controller_payload: bytes,
        route_state: dict[str, Any],
        sock: _UDPEndpoint | None,
    ) -> None:
        """Inject BRC-H900 preset-drive-speed before a preset recall.

        This lets both the web UI and the hardware controller use the route's
        stored Slow/Medium/Fast setting for preset travel, even though standard
        VISCA preset recall itself has no speed byte.
        """
        if sock is None:
            return
        preset = self._preset_number_from_recall_payload(controller_payload)
        if preset is None:
            return
        speed = self._preset_drive_speed_for_mode(route.movement_speed)
        payload = self._build_preset_speed_payload(preset, speed)
        if payload is None:
            return
        await self._send_internal_payload(idx, route, output_prof, route_state, sock, payload, f"preset {preset} speed {speed}")
        # Give the camera a beat to accept the setting before the actual recall.
        await asyncio.sleep(0.08)

    # ------------------------------------------------------------------
    # Command dispatch (manual control via API/UI)
    # ------------------------------------------------------------------

    async def send_command(self, route_index: int, command: str, args: dict[str, Any]) -> CommandResult:
        """Dispatch a manual control command to a camera route.

        Builds the correct VISCA payload, sends it via the camera socket,
        and returns what was sent. If the bridge is not running, falls back
        to building the payload without transmitting.

        Uses per-route busy tracking to prevent concurrent commands to the
        same camera (which causes VISCA sequence collisions and crashes).
        Returns result="busy" if the route is already processing a command.
        Different cameras (routes) are independent — Camera 2 can receive
        commands while Camera 1 is still moving.
        """
        if not (0 <= route_index < len(self.config.routes)):
            return CommandResult(ok=False, result="error", detail="route index out of range")
        route = self.config.routes[route_index]
        if not route.enabled:
            return CommandResult(ok=False, result="error", detail="route is disabled")

        # Check if route is busy — return immediately so the UI can show "please wait"
        if self.is_route_busy(route_index):
            return CommandResult(ok=False, result="busy", detail="camera is still moving — please wait for confirmation")

        profiles = self._resolve_profiles(route)
        output_prof = profiles[1] if profiles else None

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
                # Mark route as busy BEFORE sending
                import time
                self._route_busy[route_index] = time.monotonic()
                if command == "preset_recall":
                    asyncio.create_task(
                        self._inject_preset_speed_if_needed(
                            route_index, route, output_prof_live, payload, route_state, self._camera_sockets.get(route_index)
                        )
                    )
                cmd = {
                    "payload": payload,
                    "payload_type": 0x0100,  # VISCA_COMMAND_TYPE
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
        Address byte is omitted here - the output profile forces it to 0x81.
        """
        pan_speed = max(1, min(18, int(args.get("pan_speed", 3))))
        tilt_speed = max(1, min(17, int(args.get("tilt_speed", 3))))

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
                zoom_speed = max(1, min(7, int(args.get("zoom_speed", 3))))
                return bytes([0x01, 0x04, 0x07, 0x20 + zoom_speed, 0xFF])
            case "zoom_out":
                zoom_speed = max(1, min(7, int(args.get("zoom_speed", 3))))
                return bytes([0x01, 0x04, 0x07, 0x30 + zoom_speed, 0xFF])
            case "zoom_stop":
                return bytes([0x01, 0x04, 0x07, 0x00, 0xFF])
            case "focus_near":
                return bytes([0x01, 0x04, 0x08, 0x02, 0xFF])
            case "focus_far":
                return bytes([0x01, 0x04, 0x08, 0x03, 0xFF])
            case "focus_stop":
                return bytes([0x01, 0x04, 0x08, 0x00, 0xFF])
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
                return bytes([0x01, 0x7E, 0x01, 0x02, 0x00, 0x01, 0xFF])
            case "menu_back":
                return bytes([0x01, 0x7E, 0x01, 0x02, 0x00, 0x02, 0xFF])
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
                return CommandResult(ok=True, result="ok", detail=f"ping placeholder for {route.camera_ip}:{route.camera_port}")
            case "version":
                payload = bytes([0x09, 0x00, 0x02, 0xFF])
                self._diag["last_command"] = {
                    "route_index": route_index,
                    "command": "version_inquiry",
                    "payload_hex": payload.hex(),
                }
                self._log_event(f"[{route.label}] version inquiry")
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
