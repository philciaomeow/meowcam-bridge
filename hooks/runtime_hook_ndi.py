"""PyInstaller runtime hook for NDI DLL discovery.

In PyInstaller onedir mode the application folder is exposed via
``sys._MEIPASS``.  The NDI runtime DLL (Processing.NDI.Lib.x64.dll) is
bundled there, but Windows won't find it unless the directory is added to
the DLL search path with ``os.add_dll_directory`` (Python 3.8+ on
Windows 8.1+).

This hook is executed very early in the frozen process, before any user
import of ``NDIlib``.
"""

import os
import sys


def _add_meipass_to_dll_search_path() -> None:
    if not hasattr(sys, "_MEIPASS"):
        return  # Not running inside a PyInstaller bundle

    meipass = sys._MEIPASS
    if not meipass:
        return

    # os.add_dll_directory is available on Windows 8.1+ / Python 3.8+
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(meipass)
        except Exception:
            pass

    # Also inject into PATH for any subprocess that might spawn NDI tools
    env_path = os.environ.get("PATH", "")
    if meipass not in env_path.split(os.pathsep):
        os.environ["PATH"] = meipass + os.pathsep + env_path


_add_meipass_to_dll_search_path()
