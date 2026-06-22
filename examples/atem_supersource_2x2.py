#!/usr/bin/env python3
"""
Blackmagic ATEM SuperSource 2x2 Grid Control via PyATEMMax

Requirements:
    pip install PyATEMMax

Tested against ATEM 4 M/E Broadcast Studio 4K (20 SDI inputs, 6 AUX outputs, 1 SuperSource).

This script:
1. Connects to the ATEM switcher
2. Configures SuperSource as a 2x2 grid with 4 SDI inputs
3. Routes the SuperSource to an AUX output
4. Reads and prints input labels
5. Demonstrates toggling a camera in/out of the grid
"""

import time
import PyATEMMax
from PyATEMMax.ATEMProtocolEnums import (
    ATEMVideoSources,
    ATEMBoxes,
    ATEMAUXChannels,
)


# ─── Configuration ───────────────────────────────────────────────────────────

ATEM_IP = "192.168.1.240"  # Change to your ATEM's IP address

# Map 4 SDI inputs to the 4 grid quadrants
# ATEM 4 M/E Broadcast Studio 4K has 20 SDI inputs (input1 .. input20)
GRID_INPUTS = [
    ATEMVideoSources.input1,   # Box 1: top-left
    ATEMVideoSources.input2,   # Box 2: top-right
    ATEMVideoSources.input3,   # Box 3: bottom-left
    ATEMVideoSources.input4,   # Box 4: bottom-right
]

# 2x2 grid positions (ATEM coordinate system: X -48..48, Y -27..27)
# Size 0.5 = each box covers half the frame
GRID_POSITIONS = [
    {"x": -12.0, "y":  -6.75, "size": 0.5},  # Box 1: top-left
    {"x":  12.0, "y":  -6.75, "size": 0.5},  # Box 2: top-right
    {"x": -12.0, "y":   6.75, "size": 0.5},  # Box 3: bottom-left
    {"x":  12.0, "y":   6.75, "size": 0.5},  # Box 4: bottom-right
]

BOXES = [ATEMBoxes.box1, ATEMBoxes.box2, ATEMBoxes.box3, ATEMBoxes.box4]
AUX_TARGET = ATEMAUXChannels.auxChannel1  # Route SuperSource to AUX 1


# ─── Functions ───────────────────────────────────────────────────────────────

def configure_2x2_grid(switcher: PyATEMMax.ATEMMax):
    """Set SuperSource to a 2x2 grid with the configured inputs."""

    # Disable the SuperSource key overlay (we just want 4 boxes, no chroma key)
    switcher.setSuperSourceForeground(False)

    # Optionally set a background fill source (e.g., black or a color generator)
    # switcher.setSuperSourceFillSource(ATEMVideoSources.black)

    # Configure each box
    for i, box in enumerate(BOXES):
        pos = GRID_POSITIONS[i]
        source = GRID_INPUTS[i]

        # Enable the box
        switcher.setSuperSourceBoxParametersEnabled(box, True)

        # Set the input source for this box
        switcher.setSuperSourceBoxParametersInputSource(box, source)

        # Position the box in its quadrant
        switcher.setSuperSourceBoxParametersPositionX(box, pos["x"])
        switcher.setSuperSourceBoxParametersPositionY(box, pos["y"])

        # Set the box size (half-screen)
        switcher.setSuperSourceBoxParametersSize(box, pos["size"])

        # Disable crop (use full input frame)
        switcher.setSuperSourceBoxParametersCropped(box, False)

        print(f"  Box {i+1} ({box}) -> {source}, "
              f"pos=({pos['x']}, {pos['y']}), size={pos['size']}")

    print("SuperSource 2x2 grid configured.")


def route_to_aux(switcher: PyATEMMax.ATEMMax, aux_channel=ATEMAUXChannels.auxChannel1):
    """Route the SuperSource output to an AUX output."""
    switcher.setAuxSourceInput(aux_channel, ATEMVideoSources.superSource)
    print(f"SuperSource routed to AUX {aux_channel.value + 1}.")


def read_input_labels(switcher: PyATEMMax.ATEMMax):
    """Read and print labels for all available inputs."""
    print("\nInput labels:")
    for i in range(1, 21):  # inputs 1-20
        source = getattr(ATEMVideoSources, f"input{i}", None)
        if source is None:
            continue
        try:
            props = switcher.inputProperties[source]
            long_name = props.long_name
            short_name = props.short_name
            print(f"  Input {i:2d}: long='{long_name}' short='{short_name}'")
        except Exception:
            print(f"  Input {i:2d}: (not available)")


def toggle_box(switcher: PyATEMMax.ATEMMax, box=ATEMBoxes.box1, enabled=True):
    """Enable or disable a single SuperSource box (camera in/out of grid)."""
    switcher.setSuperSourceBoxParametersEnabled(box, enabled)
    state = "enabled" if enabled else "disabled"
    print(f"Box {box} {state} (camera {'in' if enabled else 'out of'} grid).")


def change_box_source(switcher: PyATEMMax.ATEMMax, box, new_source):
    """Swap the camera assigned to a SuperSource box on the fly."""
    switcher.setSuperSourceBoxParametersInputSource(box, new_source)
    print(f"Box {box} source changed to {new_source}.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    switcher = PyATEMMax.ATEMMax()
    print(f"Connecting to ATEM at {ATEM_IP}...")
    switcher.connect(ATEM_IP)
    switcher.waitForConnection()
    print("Connected!")

    # 1. Read input labels
    read_input_labels(switcher)

    # 2. Configure SuperSource as 2x2 grid
    print("\nConfiguring SuperSource 2x2 grid...")
    configure_2x2_grid(switcher)

    # 3. Route SuperSource to AUX output
    print("\nRouting SuperSource to AUX output...")
    route_to_aux(switcher, AUX_TARGET)

    # 4. Demo: toggle box 1 off then back on
    print("\nDemo: toggling Box 1 (camera 1) out of grid...")
    toggle_box(switcher, ATEMBoxes.box1, False)
    time.sleep(2)
    toggle_box(switcher, ATEMBoxes.box1, True)

    # 5. Demo: swap box 2's source to input 5
    print("\nDemo: swapping Box 2 source to Input 5...")
    change_box_source(switcher, ATEMBoxes.box2, ATEMVideoSources.input5)
    time.sleep(2)
    # Swap back
    change_box_source(switcher, ATEMBoxes.box2, GRID_INPUTS[1])

    print("\nDone. SuperSource is live on AUX 1.")
    print("Disconnecting...")
    switcher.disconnect()


if __name__ == "__main__":
    main()