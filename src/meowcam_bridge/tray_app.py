"""MeowCam Bridge — Windows system tray application.

Shows a tray icon with menu options:
  - Open Control Surface (opens browser to http://localhost:8080)
  - Close Server (stops the bridge and exits)

On startup, shows a small tkinter loading window that auto-closes
when the server responds to a health check.

This module is the PyInstaller entry point for the packaged Windows app.
It starts the uvicorn server in a background thread, shows the tray icon,
and manages the lifecycle.

Usage:
    python -m meowcam_bridge.tray_app

When packaged with PyInstaller, this becomes MeowCamBridge.exe.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# --- PyInstaller windowed-mode fix ---
# When console=False, sys.stdout and sys.stderr are None, which crashes
# uvicorn's logging formatter (it calls .isatty()). Redirect them to a
# log file before anything else runs.
if getattr(sys, "frozen", False) and sys.stderr is None:
    _log_dir = Path(sys.executable).resolve().parent / "logs"
    try:
        _log_dir.mkdir(exist_ok=True)
    except Exception:
        import tempfile
        _log_dir = Path(tempfile.gettempdir()) / "MeowCamBridge"
        _log_dir.mkdir(exist_ok=True)
    _console_log = open(_log_dir / "console.log", "w", encoding="utf-8")
    sys.stdout = _console_log
    sys.stderr = _console_log

logger = logging.getLogger(__name__)

# Config path: alongside the exe (PyInstaller) or cwd (dev)
if getattr(sys, "frozen", False):
    # PyInstaller --onedir: sys.executable is in dist/MeowCamBridge/MeowCamBridge.exe
    # PyInstaller --onefile: sys.executable is the exe itself; _MEIPASS is the temp extract dir
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path.cwd()

CONFIG_PATH = _BASE_DIR / "meowcam-bridge.json"
LOG_DIR = _BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "bridge.log"


def _setup_logging() -> None:
    """Set up file logging for the packaged app."""
    global LOG_DIR, LOG_FILE
    try:
        LOG_DIR.mkdir(exist_ok=True)
    except Exception:
        # If we can't create a log dir next to the exe, fall back to temp
        import tempfile
        LOG_DIR = Path(tempfile.gettempdir()) / "MeowCamBridge"
        LOG_DIR.mkdir(exist_ok=True)
        LOG_FILE = LOG_DIR / "bridge.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        ],  # No StreamHandler in windowed mode (no stdout)
    )


def _wait_for_server(host: str = "127.0.0.1", port: int = 8080, timeout: float = 15) -> bool:
    """Poll until the server responds or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.3)
    return False


def _start_server_thread(host: str, port: int, config_path: str) -> threading.Thread | None:
    """Start the uvicorn server in a background thread."""
    try:
        import uvicorn
        from meowcam_bridge.app import app, _bridge, _config_path
        import meowcam_bridge.app as app_module
        from meowcam_bridge.config import BridgeConfig
        from meowcam_bridge.bridge import BridgeCore
        import pathlib

        # Initialise the bridge (same as app.main() does)
        cfg_path = pathlib.Path(config_path)
        if cfg_path.exists():
            config = BridgeConfig.load(cfg_path)
        else:
            config = BridgeConfig()
            config.save(cfg_path)

        app_module._config_path = cfg_path
        app_module._bridge = BridgeCore(config)

        config_obj = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config_obj)

        def _run() -> None:
            try:
                server.run()
            except Exception:
                logger.exception("Server thread crashed")

        t = threading.Thread(target=_run, daemon=True, name="uvicorn-server")
        t.start()
        return t

    except Exception:
        logger.exception("Failed to start server")
        return None


def _run_tray(host: str, port: int) -> None:
    """Run the system tray icon loop."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        logger.error("pystray and Pillow are required for tray mode. Install with: pip install pystray Pillow")
        # Fallback: just keep the process alive
        print("MeowCam Bridge is running. Open http://localhost:8080 in your browser.")
        print("Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return
        return

    def _create_icon_image() -> "Image.Image":
        """Create a simple paw-print style icon."""
        img = Image.new("RGBA", (64, 64), (30, 30, 35, 255))
        draw = ImageDraw.Draw(img)
        # Main pad
        draw.ellipse([20, 28, 44, 52], fill=(100, 200, 255, 255))
        # Toe beans
        for cx, cy in [(18, 22), (26, 18), (38, 18), (46, 22)]:
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(100, 200, 255, 255))
        return img

    def _on_open(icon, item):
        webbrowser.open(f"http://localhost:{port}")

    def _on_quit(icon, item):
        logger.info("Quit requested from tray menu")
        icon.stop()
        # Server thread is daemon, will be killed when main thread exits

    menu = pystray.Menu(
        pystray.MenuItem("Open Control Surface", _on_open, default=True),
        pystray.MenuItem("Close Server", _on_quit),
    )

    icon = pystray.Icon("MeowCamBridge", _create_icon_image(), "MeowCam Bridge Server", menu)
    logger.info("Tray icon started")
    icon.run()


def _show_loading_screen(on_ready: threading.Event) -> None:
    """Show a tkinter loading window that closes when on_ready is set."""
    try:
        import tkinter as tk
    except ImportError:
        logger.warning("tkinter not available, skipping loading screen")
        on_ready.wait(timeout=15)
        return

    root = tk.Tk()
    root.title("MeowCam Bridge")
    root.geometry("320x140")
    root.resizable(False, False)

    # Centre on screen
    root.update_idinfo_events = None  # workaround for some platforms
    x = (root.winfo_screenwidth() - 320) // 2
    y = (root.winfo_screenheight() - 140) // 2
    root.geometry(f"320x140+{x}+{y}")

    # Dark background
    root.configure(bg="#1e1e23")
    frame = tk.Frame(root, bg="#1e1e23", padx=20, pady=20)
    frame.pack(expand=True, fill="both")

    label = tk.Label(
        frame,
        text="🐾 MeowCam Bridge",
        font=("Segoe UI", 16, "bold"),
        fg="#64c8ff",
        bg="#1e1e23",
    )
    label.pack(pady=(5, 5))

    status = tk.Label(
        frame,
        text="Starting server…",
        font=("Segoe UI", 10),
        fg="#888888",
        bg="#1e1e23",
    )
    status.pack(pady=(0, 5))

    def _check_ready():
        if on_ready.is_set():
            root.destroy()
            return
        root.after(200, _check_ready)

    root.after(200, _check_ready)
    root.mainloop()


def main() -> int:
    """Entry point for the tray app."""
    _setup_logging()
    logger.info("MeowCam Bridge tray app starting (v0.2.0)")
    logger.info("BASE_DIR=%s CONFIG_PATH=%s", _BASE_DIR, CONFIG_PATH)

    # Optional dependency check — log availability but don't fail
    _opt_deps = {}
    for _pkg, _mod in [
        ("ndi-python", "NDIlib"),
        ("opencv-python-headless", "cv2"),
        ("PyATEMMax", "PyATEMMax"),
    ]:
        try:
            __import__(_mod)
            _opt_deps[_pkg] = "available"
        except ImportError:
            _opt_deps[_pkg] = "missing"
    logger.info("Optional dependencies: %s", _opt_deps)

    try:
        host = "0.0.0.0"
        port = 8080

        # Start server in background thread
        server_thread = _start_server_thread(host, port, str(CONFIG_PATH))
        if server_thread is None:
            logger.error("Failed to start server thread")
            return 1

        # Show loading screen while waiting for server
        ready_event = threading.Event()

        def _wait_and_signal():
            if _wait_for_server("127.0.0.1", port, timeout=15):
                ready_event.set()
            else:
                logger.warning("Server did not become ready within 15s")
                ready_event.set()  # Close loading screen anyway

        waiter = threading.Thread(target=_wait_and_signal, daemon=True)
        waiter.start()

        _show_loading_screen(ready_event)

        # Open browser automatically on first start
        webbrowser.open(f"http://localhost:{port}")

        # Run tray icon (blocks until user quits)
        _run_tray(host, port)

        logger.info("MeowCam Bridge shutting down")
        return 0
    except Exception:
        logger.exception("Fatal error in tray app")
        return 1


if __name__ == "__main__":
    sys.exit(main())