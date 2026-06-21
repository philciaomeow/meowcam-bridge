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

| Camera Slot | Send to Bridge IP | Port |
|-------------|-------------------|------|
| 1 | (bridge PC IP) | 52381 |
| 2 | (bridge PC IP) | 52382 |
| 3 | (bridge PC IP) | 52383 |
| 4 | (bridge PC IP) | 52384 |
| 5 | (bridge PC IP) | 52385 |
| 6 | (bridge PC IP) | 52386 |
| 7 | (bridge PC IP) | 52387 |
| 8 | (bridge PC IP) | 52388 |

**How to set this on the PT-JOY-G4:**
1. Press **Menu** on the controller.
2. Navigate to **Camera Settings** or **IP Settings**.
3. For each camera slot, enter:
   - **IP Address:** The IP address of the PC running MeowCam Bridge.
   - **Port:** The port number from the table above (52381 for slot 1, etc.).
4. Save and exit.

> **Tip:** Find your bridge PC's IP address by opening Command Prompt and typing `ipconfig`. Look for "IPv4 Address" under your active network adapter.

## The web UI tabs

### Settings
- Enable/disable cameras.
- Set camera labels (e.g. "Main Stage", "Presenter").
- Enter camera IP addresses.
- Import/export your configuration.

### Presets
- Click a preset button to move the selected camera to a saved position.
- Preset labels can be edited in Settings.

### Manual Control
- Select a camera from the dropdown.
- Use arrow buttons to pan and tilt.
- Use **Zoom In / Zoom Out**.
- Use **Focus Near / Far** or **Autofocus**.
- **Save Preset** and **Recall Preset** by number.

### Diagnostics
- See the last command received and last camera reply.
- Check per-camera status (OK, Error, Unknown).
- View packet logs for troubleshooting.
- Reset a route if it gets stuck.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| Browser says "This site can't be reached" | Make sure `launch.bat` is still running. Check the black console window. |
| Camera doesn't move | Check the camera IP in Settings. Try the **Test** button next to the camera. |
| Controller has no response | Make sure the controller is set to the bridge PC's IP, not the camera's IP. |
| Windows Firewall blocked it | Go to Control Panel > Windows Defender Firewall > Allow an app. Find Python and allow it. |
| Only some cameras work | Check that each camera slot on the controller uses a different port (52381–52388). |

## Shutting down

- Close the browser tab anytime.
- To stop the bridge, click the black console window and press **Ctrl+C**, then close the window.

## Getting help

- Check the **Diagnostics** tab for error messages.
- Look in the `logs/` folder (if present) for detailed logs.
- Contact your technical support person with the Diagnostics tab open.
