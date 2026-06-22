# Research: Blackmagic ATEM SuperSource 2x2 Grid Control via Python

## ATEM Model Identification

Based on project documentation in `/opt/data/bmd-visca-bridge/docs/IMPLEMENTATION_PLAN.md`, Phil has an **ATEM 4 M/E Broadcast Studio 4K**.

Key specs relevant to SuperSource:
- 20x 12G-SDI inputs (all with auto re-sync)
- 6x auxiliary outputs
- 4 M/E rows
- **1x SuperSource** compositing engine (4 DVEs + 4 keys layered as independent video source)
- 16 ATEM Advanced Chroma Keyers
- Ethernet control (UDP 9910 protocol)
- 12G-SDI / 6G-SDI / 3G-SDI / HD-SDI auto-switching

This is a rack-mountable (2U) professional broadcast switcher, not the ATEM Mini line. It has SDI inputs (not HDMI), 6 AUX outputs, and a single SuperSource engine.

## Library Comparison: PyATEMMax vs pyatem

### PyATEMMax (clvLabs) — RECOMMENDED
- **Repo:** https://github.com/clvLabs/PyATEMMax
- **PyPI:** `pip install PyATEMMax` (v1.0b9, Sep 2022)
- **License:** GPL-3.0
- **No external dependencies** (pure Python 3)
- Port of Kasper Skarhoj's ATEMmax Arduino library
- Auto-reconnection, type hints, intellisense-friendly
- **Full SuperSource API** with 30+ SuperSource setter methods
- **AUX routing** via `setAuxSourceInput()`
- **Input label reading** via `inputProperties` state (long_name, short_name per input)
- **Input label writing** via `setInputLongName()` / `setInputShortName()`
- Thread-safe switcher state automatically updated (no polling needed)

### pyatem (OpenSwitcher / Martijn Braam)
- **Repo:** https://git.sr.ht/~martijnbraam/pyatem
- **PyPI:** `pip install pyatem` (v0.10.0)
- Lower-level protocol library (also includes GTK GUI and HTTP proxy)
- Has `SupersourceBoxPropertiesCommand` and `AuxSourceCommand`
- Event-driven: `switcher.on("change", callback)` for state updates
- Requires manual loop processing (`switcher.loop()`)
- More verbose API but gives finer control over packet timing
- Depends on `pyusb` for USB protocol support

### Blackmagic ATEM SDK (C/C++)
- Official SDK from Blackmagic Design (Windows/macOS only)
- Not Python — would require ctypes/cffi bindings or a wrapper service
- Overkill for this use case; PyATEMMax covers the same protocol

### Recommendation: **PyATEMMax**
It has the most complete Python-native SuperSource API, clean high-level methods, automatic state tracking, and no external dependencies. The pyatem library is a viable alternative if you need lower-level packet control or the HTTP proxy, but for SuperSource grid configuration PyATEMMax's setter methods are significantly easier to use.

## SuperSource Architecture

The ATEM SuperSource is a compositing engine that combines:
- **1 background (fill) source**
- **4 boxes** (each a DVE-positioned video source with optional crop/mask)
- **Optional key overlay** (chroma/luma key on top)
- **Optional border** between boxes

Each box has independent control over: enabled, input source, position X/Y, size, crop, and mask.

For a **2x2 grid**, all 4 boxes are enabled, each assigned to one camera input, positioned at the four quadrants of the screen with equal size.

### ATEM Video Source Indices (PyATEMMax constants)

| Constant | Value | Description |
|----------|-------|-------------|
| `input1` through `input20` | 1-20 | SDI inputs 1-20 |
| `superSource` | 6000 | SuperSource output (for routing to AUX/PGM) |
| `auxilary1` through `auxilary6` | 8001-8006 | AUX outputs 1-6 |
| `color1`, `color2` | 2001, 2002 | Color generators |
| `black` | 0 | Black source |

### ATEM Box Constants

| Constant | Value |
|----------|-------|
| `box1` | 0 |
| `box2` | 1 |
| `box3` | 2 |
| `box4` | 3 |

### AUX Channel Constants

| Constant | Value |
|----------|-------|
| `auxChannel1` | 0 |
| `auxChannel2` | 1 |
| ... | ... |
| `auxChannel6` | 5 |

## 2x2 Grid Positioning

For a 16:9 frame, the ATEM coordinate system uses:
- X range: -48.0 to +48.0 (left to right, 0 = center)
- Y range: -27.0 to +27.0 (top to bottom, 0 = center)
- Size range: 0.07 to 1.0 (1.0 = full frame)

For a 2x2 grid with each box at half-screen size:
- Box size: 0.5 (each box covers half the frame width)
- Positions (centered in each quadrant):
  - Box 1 (top-left):     X=-12.0, Y=-6.75
  - Box 2 (top-right):    X=+12.0, Y=-6.75
  - Box 3 (bottom-left):  X=-12.0, Y=+6.75
  - Box 4 (bottom-right): X=+12.0, Y=+6.75

These values place each box's center at the midpoint of its quadrant. The size of 0.5 means each box occupies half the frame's horizontal span. You may need to tweak size slightly (e.g., 0.48) to leave a small gap or border between boxes.

## Capabilities Confirmed

1. **Set SuperSource layout to 2x2**: YES — enable all 4 boxes, set positions and sizes
2. **Assign 4 SDI inputs to 4 quadrants**: YES — `setSuperSourceBoxParametersInputSource(box, source)` per box
3. **Route SuperSource to AUX output**: YES — `setAuxSourceInput(auxChannel, superSource)`
4. **Read input labels**: YES — `switcher.inputProperties[source].long_name` / `.short_name`
5. **Switch individual cameras in/out of grid**: YES — enable/disable individual boxes with `setSuperSourceBoxParametersEnabled(box, enabled)`, or change the source assigned to a box on the fly
6. **Set input labels**: YES — `setInputLongName(source, name)` / `setInputShortName(source, name)`