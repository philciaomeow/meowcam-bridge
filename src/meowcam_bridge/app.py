"""Application entry point and FastAPI stub.

Starts the web UI and (in future) the bridge core.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .config import BridgeConfig
from .bridge import BridgeCore

# FastAPI app (module-level so uvicorn can import it)
app = FastAPI(title="MeowCam Bridge", version="0.1.0")

# In-memory config and bridge core (replaced on reload)
_bridge: BridgeCore | None = None


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    """Serve the embedded web UI."""
    html_path = pathlib.Path(__file__).with_suffix("").parent / "web" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<html><body><h1>MeowCam Bridge</h1><p>UI not found.</p></body></html>"


@app.get("/api/config")
async def get_config() -> dict:
    """Return current bridge configuration."""
    if _bridge is None:
        return {"error": "bridge not initialised"}
    return _bridge.config.model_dump()


@app.get("/api/routes")
async def get_routes() -> list[dict]:
    """Return camera routes with status."""
    if _bridge is None:
        return []
    return [
        {"index": i, **route.model_dump()}
        for i, route in enumerate(_bridge.config.routes)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="MeowCam Bridge")
    parser.add_argument("--config", default="meowcam-bridge.json", help="Path to config file")
    parser.add_argument("--host", default="0.0.0.0", help="Web UI bind address")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port")
    args = parser.parse_args()

    config_path = pathlib.Path(args.config)
    if config_path.exists():
        config = BridgeConfig.load(config_path)
    else:
        config = BridgeConfig()
        config.save(config_path)

    global _bridge
    _bridge = BridgeCore(config)

    # Serve static web assets if present
    web_dir = pathlib.Path(__file__).with_suffix("").parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
