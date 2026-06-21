#!/usr/bin/env bash
# MeowCam Bridge — POSIX launcher (Linux / macOS)
# Usage: ./launch.sh [--config path.json]

set -euo pipefail

CONFIG_FILE="${2:-meowcam-bridge.json}"
HOST="0.0.0.0"
PORT="8080"

echo "=========================================="
echo "  MeowCam Bridge - Starting up..."
echo "=========================================="
echo

# --- Check for Python ---
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed."
    echo
    echo "Install Python 3.11+ and try again:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "  macOS:         brew install python3"
    echo
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "Found: $PYTHON_VERSION"
echo

# --- Optional: use venv ---
if [[ ! -d ".venv" ]]; then
    echo "No .venv found. Create one now? [y/N]"
    read -r REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        python3 -m venv .venv
        echo "Virtual environment created."
    fi
fi

if [[ -d ".venv" ]]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
    echo "Using virtual environment."
fi

# --- Install / update package ---
if [[ -f "pyproject.toml" ]]; then
    echo "Installing / updating MeowCam Bridge (editable)..."
    python3 -m pip install -e ".[dev]" --quiet || echo "WARNING: pip install failed. Trying to run anyway..."
    echo "OK."
else
    echo "No pyproject.toml found. Assuming package is already installed."
fi
echo

# --- Config file ---
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "No config found at $CONFIG_FILE."
    echo "A default config will be created on first run."
    echo "You can edit $CONFIG_FILE later or use the Settings tab in the web UI."
    echo
fi

# --- Start bridge ---
echo "Starting MeowCam Bridge..."
echo "Web UI will be available at: http://localhost:$PORT"
echo
echo "Press Ctrl+C to stop."
echo

# Open browser after a short delay (macOS and Linux compatible)
(
    sleep 3
    if command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:$PORT" &>/dev/null || true
    elif command -v open &>/dev/null; then
        open "http://localhost:$PORT" &>/dev/null || true
    fi
) &

# Run the bridge
python3 -m meowcam_bridge --config "$CONFIG_FILE" --host "$HOST" --port "$PORT"
