# MeowCam Bridge — Onsite Setup Guide

> For non-technical operators. If you are packaging or developing, see `PACKAGING.md`.

## What you need

- A Windows PC or laptop with network access to the cameras and controller.
- The MeowCam Bridge folder (unzipped).
- PTZOptics PT-JOY-G4 controller.
- Up to 8 Sony BRC-H900 cameras with BRBK-IP10 IP cards.
- Network cables / switch connecting everything together.
- Optional: USB HDMI capture card (e.g. Blackmagic, Elgato) for live video preview.
- Optional: ATEM switcher on the network for tally indicators.

## Quick start (5 minutes)

1. **Unzip** the MeowCam Bridge folder to your Desktop or Documents.
2. **Double-click** `MeowCamBridge.exe`.
3. A loading screen appears briefly, then a system tray icon shows "MeowCam Bridge".
4. If Windows Firewall asks, click **Allow**.
5. Your web browser will open to `http://localhost:8080`.
6. Go to the **Settings** tab and enter your camera IPs.
7. Click **Save**, then go to **Preview** to see live video or **Manual Control** to test.

> **No Python installation needed.** The `.exe` bundles everything.

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

## Video setup (optional)

### NDI sources

If you have an ATEM switcher, OBS, or other NDI sender on the network:

1. Go to **Settings → Camera Video** for the camera you want to configure.
2. Set **Source Type** to **NDI Stream**.
3. Click **Discover Sources** — a dropdown will appear with available NDI streams.
4. Select the NDI source for that camera.
5. Choose a **Crop/Region** preset (Full Frame, or a quadrant if splitting a multiview).
6. Click **Save**.

### USB / HDMI capture cards

If you have a USB capture card (Blackmagic, Elgato, etc.) connected to the bridge PC:

1. Go to **Settings → Camera Video** for the camera you want to configure.
2. Set **Source Type** to **USB / HDMI Capture**.
3. A **device dropdown** will appear showing available capture cards.
4. Select your capture device.
5. Choose a **Crop/Region** preset.
6. Click **Save**.

> **Shared USB:** Multiple cameras can share ONE capture device. For example, if your ATEM outputs a 2×2 multiview over HDMI to a single capture card, set Camera 1 to Top-Left, Camera 2 to Top-Right, Camera 3 to Bottom-Left, and Camera 4 to Bottom-Right. The bridge reads one feed and crops each camera's region.

### Crop/Region presets

| Preset | What it shows |
|--------|---------------|
| **Full Frame** 🖼️ | Entire video source |
| **Top-Left** ↖️ | Top-left quarter |
| **Top-Right** ↗️ | Top-right quarter |
| **Bottom-Left** ↙️ | Bottom-left quarter |
| **Bottom-Right** ↘️ | Bottom-right quarter |
| **Custom** ⚙️ | Manual crop region (advanced) |

### Output Resolution

The **Output Resolution (preview size)** setting controls how large each preview thumbnail is in the web UI. Lower resolutions use less bandwidth and load faster. This does not affect the camera or video source quality — only the preview display size.

## The web UI tabs

### Preview
- Live 2×2 grid showing real video feeds from each camera.
- Click any camera to enlarge it.
- Red border = camera is on PGM (live program), green border = PVW (preview) — requires ATEM integration.

### Presets
- Four cameras shown at a time in large touch-friendly cards.
- Small video preview thumbnail at the top of each column.
- Click a preset button to move the camera to a saved position.
- **Slow / Medium / Fast** speed buttons per camera card — these control both manual movement speed AND preset travel speed.
- Preset labels can be renamed via **Edit preset names** mode.
- Last-recalled preset stays highlighted per camera.
- Use **Cameras 1–4 / 5–8** buttons to switch between camera groups.

### Manual Control
- Select a camera from the dropdown.
- **Slow / Medium / Fast** buttons set the speed mode (saved to this camera automatically).
- Pan/tilt buttons are **hold-to-move, release-to-stop** (like a real joystick).
- Use **Zoom In / Zoom Out**, **Focus Near / Far**, or **Autofocus Toggle**.
- **OSD Menu** buttons: Open, Enter, Back, Close.
- **Save Preset** and **Recall Preset** by number.

### Diagnostics
- See the last command received and last camera reply.
- Check per-camera status (OK, Error, Unknown).
- View packet logs for troubleshooting.
- Reset route states if needed.

### Settings
- **Camera Control:** Enable/disable cameras, labels, IP addresses, ports, profiles.
- **Camera Video:** Source type (NDI/USB/Test Pattern), NDI source discovery, USB device selection, crop/region presets, output resolution.
- **ATEM:** ATEM switcher IP, SuperSource configuration, tally settings.
- Import/export your configuration as a JSON file.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| Browser says "This site can't be reached" | Make sure `MeowCamBridge.exe` is still running. Check the system tray icon. |
| Camera doesn't move | Check the camera IP in Settings. Try the **Test** button next to the camera. |
| Controller has no response | Make sure the controller is set to the bridge PC's IP, not the camera's IP. Check the controller profile is set to VISCA(UDP), not Sony VISCA(UDP). |
| Windows Firewall blocked it | Go to Control Panel > Windows Defender Firewall > Allow an app. Find MeowCamBridge.exe and allow it. |
| Only some cameras work | Check that each camera slot on the controller uses a different port (52382–52389). Make sure each camera is enabled in Settings. |
| Preset speed doesn't change | Make sure you've selected Slow/Medium/Fast for that camera in the Presets tab or Manual Control. |
| No video in preview | Check that the video source is enabled in Settings → Camera Video. For NDI, click Discover Sources. For USB, check the capture card is connected. |
| NDI sources not found | Ensure the NDI sender (ATEM, OBS) is on the same network. On Windows, NDI uses native mDNS — no extra setup needed. |
| USB capture card not detected | Check the device appears in the dropdown. Try unplugging and replugging. Ensure drivers are installed. |
| OSD Enter doesn't work from controller | The bridge translates controller OSD Enter automatically. If it still doesn't work, try the web UI OSD Enter button in Manual Control. |

## Shutting down

- Close the browser tab anytime.
- To stop the bridge: **Right-click the system tray icon** and select **Close Server**.

## Getting help

- Check the **Diagnostics** tab for error messages.
- Contact your technical support person with the Diagnostics tab open.
