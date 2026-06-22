# MeowCam Bridge — Onsite Setup Guide

> For non-technical operators. If you are packaging or developing, see `PACKAGING.md`.

## What you need

- A Windows PC or laptop with network access to the cameras and controller.
- The MeowCam Bridge folder (unzipped).
- PTZOptics PT-JOY-G4 controller.
- Up to 8 Sony BRC-H900 cameras with BRBK-IP10 IP cards.
- Network cables / switch connecting everything together.

## Quick start (5 minutes)

1. **Unzip** the MeowCam Bridge folder to your Desktop or Documents.
2. **Double-click** `launch.bat` (Windows).
3. If Windows Firewall asks, click **Allow**.
4. Your web browser will open to `http://localhost:8080`.
5. Go to the **Settings** tab and enter your camera IPs.
6. Click **Save**, then go to **Manual Control** to test.

> **Coming in v0.2:** A system tray app with a GUI loading screen — no command window, just a tray icon with "Open Control Surface" and "Close Server" options. Python will be bundled, so no separate install needed.

## Network setup

### Camera IP addresses

Each camera needs a static IP on the same network as the bridge PC. Default example IPs:

| Camera | IP Address | Port |
|--------|-----------|------|
| Camera 1 | 192.168.1.100 | 52381 |
| Camera 2 | 192.168.1.101 | 52381 |
| ... | ... | ... |
| Camera 8 | 192.168.1.107 | 52381 |

> The camera port is always **52381** for Sony BRBK-IP10 cards.

### Controller setup (PT-JOY-G4)

The controller sends VISCA commands to the **bridge PC's IP address**, not directly to the cameras. Each camera uses a different port so the bridge knows which camera you mean.

**Recommended: Generic VISCA(UDP) mode** (allows custom ports for multi-camera):

| Camera Slot | Send to Bridge IP | Port | Controller Profile |
|-------------|-------------------|------|-------------------|
| 1 | (bridge PC IP) | 52382 | VISCA(UDP) |
| 2 | (bridge PC IP) | 52383 | VISCA(UDP) |
| 3 | (bridge PC IP) | 52384 | VISCA(UDP) |
| 4 | (bridge PC IP) | 52385 | VISCA(UDP) |
| 5 | (bridge PC IP) | 52386 | VISCA(UDP) |
| 6 | (bridge PC IP) | 52387 | VISCA(UDP) |
| 7 | (bridge PC IP) | 52388 | VISCA(UDP) |
| 8 | (bridge PC IP) | 52389 | VISCA(UDP) |

> **Important:** Use the generic **VISCA(UDP)** profile on the controller, **not** "Sony VISCA(UDP)". The Sony profile locks to port 52381 and doesn't allow per-camera ports. The generic VISCA(UDP) profile sends raw VISCA bytes which the bridge translates to Sony format.

**How to set this on the PT-JOY-G4:**
1. Open the controller's web interface (point your browser to the controller's IP).
2. For each camera channel, set:
   - **Protocol:** VISCA(UDP)
   - **IP Address:** The IP address of the PC running MeowCam Bridge.
   - **Port:** The port number from the table above (52382 for slot 1, etc.).
3. Save settings.

> **Tip:** Find your bridge PC's IP address by opening Command Prompt and typing `ipconfig`. Look for "IPv4 Address" under your active network adapter.

## The web UI tabs

### Presets
- Four cameras shown at a time in large touch-friendly cards.
- Click a preset button to move the camera to a saved position.
- **Slow / Medium / Fast** speed buttons per camera card — these control both manual movement speed AND preset travel speed.
- Preset labels can be renamed via **Edit preset names** mode.
- Last-recalled preset stays highlighted per camera.
- Use **Cameras 1–4 / 5–8** buttons to switch between camera groups.

### Manual Control
- Select a camera from the dropdown.
- **Slow / Medium / Fast** buttons set the speed mode (saved to this camera automatically).
- **Save speed for this camera** button confirms the current speed selection.
- Pan/tilt buttons are **hold-to-move, release-to-stop** (like a real joystick).
- Use **Zoom In / Zoom Out**, **Focus Near / Far**, or **Autofocus Toggle**.
- **OSD Menu** buttons: Open, Enter, Back, Close.
- **Save Preset** and **Recall Preset** by number.

### Diagnostics
- See the last command received and last camera reply.
- Check per-camera status (OK, Error, Unknown).
- View packet logs for troubleshooting — shows controller RX, camera TX, camera replies, and internal preset-speed commands.
- Reset route states if needed.

### Settings
- Enable/disable cameras.
- Set camera labels (e.g. "Main Stage", "Presenter").
- Enter camera IP addresses and ports.
- Choose controller input profile and camera output profile per route.
- Set the bridge IP address (which network interface to bind to).
- Import/export your configuration as a JSON file.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| Browser says "This site can't be reached" | Make sure `launch.bat` is still running. Check the console window or system tray icon. |
| Camera doesn't move | Check the camera IP in Settings. Try the **Test** button next to the camera. |
| Controller has no response | Make sure the controller is set to the bridge PC's IP, not the camera's IP. Check the controller profile is set to VISCA(UDP), not Sony VISCA(UDP). |
| Windows Firewall blocked it | Go to Control Panel > Windows Defender Firewall > Allow an app. Find Python (or the MeowCam Bridge exe) and allow it. |
| Only some cameras work | Check that each camera slot on the controller uses a different port (52382–52389). Make sure each camera is enabled in Settings. |
| Preset speed doesn't change | Make sure you've selected Slow/Medium/Fast for that camera in the Presets tab or Manual Control. The speed is saved per-camera. |
| OSD Enter doesn't work from controller | The bridge translates controller OSD Enter automatically. If it still doesn't work, try the web UI OSD Enter button in Manual Control. |

## Shutting down

- Close the browser tab anytime.
- To stop the bridge:
  - **v0.1:** Click the console window and press **Ctrl+C**, then close the window.
  - **v0.2 (coming):** Right-click the system tray icon and select **Close Server**.

## Getting help

- Check the **Diagnostics** tab for error messages.
- Contact your technical support person with the Diagnostics tab open.