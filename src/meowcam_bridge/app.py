"""Application entry point and FastAPI API shell.

Starts the web UI and exposes REST endpoints for settings, manual control,
presets, diagnostics, and config import/export.
"""

from __future__ import annotations

import argparse
import pathlib
import socket
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .config import BridgeConfig, CameraRoute, MAX_ROUTES
from .bridge import BridgeCore, CommandResult

# FastAPI app (module-level so uvicorn can import it)
app = FastAPI(title="MeowCam Bridge", version="0.1.0")

# In-memory config and bridge core (replaced on reload)
_bridge: BridgeCore | None = None
_config_path: pathlib.Path | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_config() -> None:
    if _bridge is not None and _config_path is not None:
        _bridge.config.save(_config_path)


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


# ---------------------------------------------------------------------------
# Network interfaces
# ---------------------------------------------------------------------------

@app.get("/api/network-interfaces")
async def get_network_interfaces() -> list[dict]:
    """Return local network interface IPs for the bridge IP dropdown."""
    interfaces = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip == "127.0.0.1":
                continue
            name = "interface"
            try:
                import netifaces
                for iface_name in netifaces.interfaces():
                    addrs = netifaces.ifaddresses(iface_name)
                    ipv4s = addrs.get(netifaces.AF_INET, [])
                    for addr in ipv4s:
                        if addr.get("addr") == ip:
                            name = iface_name
                            break
            except ImportError:
                pass
            if not any(i["ip"] == ip for i in interfaces):
                interfaces.append({"ip": ip, "name": name})
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

    global _config_path, _bridge
    _config_path = pathlib.Path(args.config)
    if _config_path.exists():
        config = BridgeConfig.load(_config_path)
    else:
        config = BridgeConfig()
        config.save(_config_path)

    _bridge = BridgeCore(config)

    # Serve static web assets if present
    web_dir = pathlib.Path(__file__).with_suffix("").parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
