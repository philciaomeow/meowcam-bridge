"""Application entry point and FastAPI API shell.

Starts the web UI and exposes REST endpoints for settings, manual control,
presets, diagnostics, and config import/export.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import pathlib
import socket
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .config import BridgeConfig, CameraRoute, MAX_ROUTES
from .bridge import BridgeCore, CommandResult
from .video_manager import VideoSourceManager
from .video import mjpeg_generator, snapshot_response, NDISource
from .atem import ATEMManager, ATEMConnectionError

# In-memory config and bridge core (replaced on reload)
_bridge: BridgeCore | None = None
_config_path: pathlib.Path | None = None
_video_manager: VideoSourceManager | None = None
_atem_manager: ATEMManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop the bridge UDP listeners alongside the web UI."""
    global _bridge, _video_manager
    if _bridge is not None:
        await _bridge.start()
        print(f"[MeowCam] Bridge started — {_bridge.config}")
    if _video_manager is not None:
        _video_manager.start()
        print("[MeowCam] Video manager started")
    yield
    if _video_manager is not None:
        _video_manager.stop()
        print("[MeowCam] Video manager stopped")
    if _bridge is not None:
        await _bridge.stop()
        print("[MeowCam] Bridge stopped.")


# FastAPI app (module-level so uvicorn can import it)
app = FastAPI(title="MeowCam Bridge", version="0.1.0", lifespan=lifespan)

# Serve static web assets (CSS/JS) — mounted at module level so both
# main() and tray_app.py entry points serve them correctly.
_web_dir = pathlib.Path(__file__).with_suffix("").parent / "web"
if _web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_web_dir)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_config() -> None:
    if _bridge is not None and _config_path is not None:
        _bridge.config.save(_config_path)
    if _video_manager is not None:
        _video_manager.on_config_changed()


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    """Serve the embedded web UI."""
    html_path = pathlib.Path(__file__).with_suffix("").parent / "web" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<html><body><h1>MeowCam Bridge</h1><p>UI not found.</p></body></html>"


# ---------------------------------------------------------------------------
# Video streaming
# ---------------------------------------------------------------------------

@app.get("/api/video/feed/{route_index}")
async def video_feed(
    route_index: int,
    fps: int | None = None,
    quality: int = 60,
    width: int = 480,
) -> StreamingResponse:
    """MJPEG stream for a camera route."""
    if _video_manager is None:
        raise HTTPException(status_code=503, detail="video manager not initialised")
    source = _video_manager.get_source(route_index)
    if source is None:
        raise HTTPException(status_code=404, detail="no video source for this route")

    # Fall back to route config for fps if not provided
    target_fps = fps
    if target_fps is None and _bridge is not None and 0 <= route_index < len(_bridge.config.routes):
        target_fps = _bridge.config.routes[route_index].video.frame_rate
    if target_fps is None or target_fps <= 0:
        target_fps = 8

    return StreamingResponse(
        mjpeg_generator(source, fps=target_fps, quality=quality, width=width),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/video/snapshot/{route_index}")
async def video_snapshot(
    route_index: int,
    quality: int = 60,
    width: int = 480,
) -> StreamingResponse:
    """Single JPEG snapshot for a camera route."""
    if _video_manager is None:
        raise HTTPException(status_code=503, detail="video manager not initialised")
    source = _video_manager.get_source(route_index)
    if source is None:
        raise HTTPException(status_code=404, detail="no video source for this route")
    jpeg, media_type = snapshot_response(source, quality=quality, width=width)
    return StreamingResponse(io.BytesIO(jpeg), media_type=media_type)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config() -> dict:
    """Return current bridge configuration."""
    if _bridge is None:
        return {"error": "bridge not initialised"}
    return _bridge.config.model_dump()


@app.put("/api/config")
async def put_config(payload: dict) -> dict:
    """Replace the entire bridge configuration."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    try:
        cfg = BridgeConfig.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid config: {exc}")
    _bridge.config = cfg
    _save_config()
    return _bridge.config.model_dump()


@app.post("/api/config/export")
async def export_config() -> JSONResponse:
    """Return the current config as a downloadable JSON blob."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    return JSONResponse(
        content=_bridge.config.model_dump(),
        headers={"Content-Disposition": 'attachment; filename="meowcam-bridge.json"'},
    )


@app.post("/api/config/import")
async def import_config(payload: dict) -> dict:
    """Import a full config JSON object and replace the current one."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    try:
        cfg = BridgeConfig.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid config: {exc}")
    _bridge.config = cfg
    _save_config()
    return _bridge.config.model_dump()


# ---------------------------------------------------------------------------
# Routes (camera rows)
# ---------------------------------------------------------------------------

@app.get("/api/routes")
async def get_routes() -> list[dict]:
    """Return camera routes with status."""
    if _bridge is None:
        return []
    return [
        {"index": i, **route.model_dump()}
        for i, route in enumerate(_bridge.config.routes)
    ]


@app.put("/api/routes/{index}")
async def put_route(index: int, payload: dict) -> dict:
    """Update a single camera route by index."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    if index < 0 or index >= MAX_ROUTES:
        raise HTTPException(status_code=400, detail=f"index must be 0..{MAX_ROUTES - 1}")
    # Ensure the routes list is long enough
    while len(_bridge.config.routes) <= index:
        _bridge.config.routes.append(CameraRoute())
    try:
        updated = CameraRoute.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid route: {exc}")
    _bridge.config.routes[index] = updated
    _save_config()
    return {"index": index, **updated.model_dump()}


@app.delete("/api/routes/{index}")
async def delete_route(index: int) -> dict:
    """Remove a camera route by index."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    if 0 <= index < len(_bridge.config.routes):
        _bridge.config.routes.pop(index)
        _save_config()
    return {"index": index, "deleted": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@app.post("/api/routes/{index}/test")
async def test_route(index: int, payload: dict | None = None) -> dict:
    """Run a test against a camera route.

    Payload (optional):
      - "type": "ping" | "version" | "stop" (default "version")
    Returns test result details.
    """
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    if index < 0 or index >= len(_bridge.config.routes):
        raise HTTPException(status_code=404, detail="route not found")
    route = _bridge.config.routes[index]
    test_type = (payload or {}).get("type", "version")
    result = await _bridge.test_route(index, test_type)
    return {
        "index": index,
        "route_label": route.label,
        "test_type": test_type,
        "result": result.result,
        "detail": result.detail,
        "ok": result.ok,
    }


# ---------------------------------------------------------------------------
# Manual control & presets
# ---------------------------------------------------------------------------

@app.post("/api/command")
async def post_command(payload: dict) -> dict:
    """Send a manual control command to a camera route.

    Expected payload:
      - "route_index": int   (required)
      - "command": str       (required)
      - "args": dict         (optional, command-specific)

    Commands:
      pan_left, pan_right, tilt_up, tilt_down, stop,
      zoom_in, zoom_out, focus_near, focus_far, autofocus_toggle,
      preset_save, preset_recall, menu_open, menu_enter, menu_back
    """
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    route_index = payload.get("route_index")
    command = payload.get("command")
    args = payload.get("args", {})
    if route_index is None or command is None:
        raise HTTPException(status_code=422, detail="route_index and command are required")
    if not (0 <= route_index < len(_bridge.config.routes)):
        raise HTTPException(status_code=404, detail="route not found")
    result = await _bridge.send_command(route_index, command, args)
    return {
        "route_index": route_index,
        "command": command,
        "ok": result.ok,
        "detail": result.detail,
    }


@app.get("/api/ndi/sources")
async def ndi_sources() -> dict:
    """Discover available NDI sources on the network."""
    try:
        import NDIlib as _ndi  # type: ignore[import-untyped]
    except ImportError:
        return {"sources": [], "available": False, "error": "ndi-python not installed"}

    try:
        # Use ref-counting — never call destroy (NDIlib double-free bug)
        NDISource._ensure_ndi_init(_ndi)

        ndi_find = _ndi.find_create_v2()
        if ndi_find is None:
            return {"sources": [], "available": False, "error": "find_create_v2() failed"}

        sources = []
        for _ in range(3):
            _ndi.find_wait_for_sources(ndi_find, 1000)
            sources = _ndi.find_get_current_sources(ndi_find)

        result = []
        for s in sources:
            result.append({
                "ndi_name": s.ndi_name,
                "url_address": s.url_address,
            })

        _ndi.find_destroy(ndi_find)
        # Do NOT call _ndi.destroy() — known NDIlib double-free bug
        return {"sources": result, "available": True}
    except Exception as exc:
        return {"sources": [], "available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Network interfaces
# ---------------------------------------------------------------------------

@app.get("/api/network-interfaces")
async def get_network_interfaces() -> list[dict]:
    """Return local network interface IPs for the bridge IP dropdown."""
    interfaces = []
    # Primary: use netifaces to enumerate ALL interfaces properly
    try:
        import netifaces
        for iface_name in netifaces.interfaces():
            if iface_name == "lo":
                continue
            addrs = netifaces.ifaddresses(iface_name)
            ipv4s = addrs.get(netifaces.AF_INET, [])
            for addr in ipv4s:
                ip = addr.get("addr", "")
                if ip == "127.0.0.1" or ip.startswith("127."):
                    continue
                if not any(i["ip"] == ip for i in interfaces):
                    interfaces.append({"ip": ip, "name": iface_name})
    except ImportError:
        # Fallback: hostname resolution + socket trick
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if ip == "127.0.0.1" or ip.startswith("127."):
                    continue
                if not any(i["ip"] == ip for i in interfaces):
                    interfaces.append({"ip": ip, "name": "interface"})
        except Exception:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if not any(i["ip"] == ip for i in interfaces):
                interfaces.append({"ip": ip, "name": "primary"})
        except Exception:
            pass
    return interfaces


# ---------------------------------------------------------------------------
# Bridge control
# ---------------------------------------------------------------------------

@app.post("/api/bridge/restart")
async def restart_bridge() -> dict:
    """Restart the UDP bridge listeners after config changes."""
    global _bridge
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    await _bridge.stop()
    await _bridge.start()
    routes_info = [{"index": i, "label": r.label, "enabled": r.enabled, "status": r.status}
                   for i, r in enumerate(_bridge.config.routes)]
    return {"ok": True, "routes": routes_info}


@app.get("/api/bridge/status")
async def bridge_status() -> dict:
    """Check if the UDP bridge is running."""
    if _bridge is None:
        return {"running": False}
    return {"running": _bridge._running, "routes": len(_bridge.config.routes)}


# ---------------------------------------------------------------------------
# ATEM switcher — SuperSource, tally, inputs
# ---------------------------------------------------------------------------

def _get_atem() -> ATEMManager:
    """Return the active ATEM manager or raise 503."""
    if _atem_manager is None or not _atem_manager.connected:
        raise HTTPException(status_code=503, detail="ATEM not connected")
    return _atem_manager


@app.get("/api/atem/status")
async def atem_status() -> dict:
    """Return ATEM connection status."""
    if _atem_manager is None:
        if _bridge is not None:
            return {"connected": False, "enabled": _bridge.config.atem.enabled, "atem_ip": _bridge.config.atem.atem_ip}
        return {"connected": False, "enabled": False}
    return _atem_manager.status()


@app.post("/api/atem/connect")
async def atem_connect() -> dict:
    """Connect to the ATEM switcher using the configured IP."""
    global _atem_manager
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    if not _bridge.config.atem.enabled:
        raise HTTPException(status_code=400, detail="ATEM is disabled in config")
    if _atem_manager is not None and _atem_manager.connected:
        return {"connected": True, "atem_ip": _bridge.config.atem.atem_ip}
    try:
        _atem_manager = ATEMManager(_bridge.config.atem)
        _atem_manager.connect()
    except ATEMConnectionError as exc:
        raise HTTPException(status_code=502, detail=f"ATEM connection failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ATEM connection error: {exc}")
    return {"connected": True, "atem_ip": _bridge.config.atem.atem_ip}


@app.post("/api/atem/disconnect")
async def atem_disconnect() -> dict:
    """Disconnect from the ATEM switcher."""
    global _atem_manager
    if _atem_manager is not None:
        _atem_manager.disconnect()
        _atem_manager = None
    return {"connected": False}


@app.get("/api/atem/supersource")
async def get_supersource() -> dict:
    """Read current SuperSource box configuration."""
    manager = _get_atem()
    try:
        return manager.get_supersource_state()
    except ATEMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.put("/api/atem/supersource")
async def put_supersource(payload: dict) -> dict:
    """Configure SuperSource as a 2x2 grid.

    Optional payload:
      - "input_mapping": [int, int, int, int]  (4 ATEM SDI input numbers 1-20)
      - "aux_output": int  (1-6, overrides config)
    """
    manager = _get_atem()
    input_mapping = payload.get("input_mapping")
    aux_output = payload.get("aux_output")
    try:
        result = manager.configure_supersource_2x2(input_mapping)
        if aux_output is not None:
            manager.route_supersource_to_aux(aux_output)
            result["aux_output"] = aux_output
        else:
            result["aux_output"] = manager._config.supersource_aux_output
        return result
    except ATEMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/atem/supersource/box/{box_index}/toggle")
async def toggle_box(box_index: int, payload: dict | None = None) -> dict:
    """Toggle a SuperSource box on/off.

    Optional payload:
      - "enabled": bool  (explicit on/off; if omitted, toggles current state)
    """
    manager = _get_atem()
    enabled = (payload or {}).get("enabled")
    try:
        return manager.toggle_box(box_index, enabled)
    except ATEMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/atem/tally")
async def get_tally() -> dict:
    """Read current PGM/PVW source for M/E row 0."""
    manager = _get_atem()
    try:
        return manager.get_tally(0)
    except ATEMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/atem/inputs")
async def get_atem_inputs() -> list[dict]:
    """Read input labels for all 20 SDI inputs."""
    manager = _get_atem()
    try:
        return manager.get_inputs()
    except ATEMConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/atem/config")
async def get_atem_config() -> dict:
    """Return the ATEM configuration section."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    return _bridge.config.atem.model_dump()


@app.put("/api/atem/config")
async def put_atem_config(payload: dict) -> dict:
    """Update the ATEM configuration section."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    from .config import AtemConfig
    try:
        updated = AtemConfig.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid ATEM config: {exc}")
    _bridge.config.atem = updated
    _save_config()
    return _bridge.config.atem.model_dump()


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@app.get("/api/diagnostics")
async def get_diagnostics() -> dict:
    """Return current diagnostics state."""
    if _bridge is None:
        return {"error": "bridge not initialised"}
    return _bridge.diagnostics()


@app.post("/api/diagnostics/reset")
async def reset_diagnostics() -> dict:
    """Reset per-route state and diagnostics counters."""
    if _bridge is None:
        raise HTTPException(status_code=503, detail="bridge not initialised")
    _bridge.reset_diagnostics()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="MeowCam Bridge")
    parser.add_argument("--config", default="meowcam-bridge.json", help="Path to config file")
    parser.add_argument("--host", default="0.0.0.0", help="Web UI bind address")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port")
    args = parser.parse_args()

    global _config_path, _bridge, _video_manager
    _config_path = pathlib.Path(args.config)
    if _config_path.exists():
        config = BridgeConfig.load(_config_path)
    else:
        config = BridgeConfig()
        config.save(_config_path)

    _bridge = BridgeCore(config)
    _video_manager = VideoSourceManager(config)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
