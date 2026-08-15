# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Riplox Desktop.

onedir, not onefile: a onefile build unpacks itself to a temp folder on every
launch, which is both slower and the exact behaviour antivirus heuristics
punish. onedir also keeps the ffmpeg DLLs sitting next to ffmpeg.exe, which
the shared build requires.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

webview_datas, webview_binaries, webview_hidden = collect_all("webview")

# bin/ is deliberately NOT bundled here. PyInstaller reclassifies DLLs found in
# datas as binaries and copies them to the top level as well, which duplicated
# ~160 MB of ffmpeg. The installer places bin/ beside the exe instead, and
# engine.bundle_roots() looks there.
datas = [
    (str(SRC / "templates"), "templates"),
    (str(SRC / "static"), "static"),
] + webview_datas

# Anything pulled in transitively that this app never touches. Left in, these
# add hundreds of MB and have repeatedly caused boot crashes in past builds.
excludes = [
    "numpy", "pandas", "matplotlib", "scipy", "cv2",
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "notebook", "IPython", "pytest", "sqlite3", "test", "unittest",
    "pydoc_data", "lib2to3", "distutils",
]

a = Analysis(
    [str(SRC / "app.py")],
    pathex=[str(SRC)],
    binaries=webview_binaries,
    datas=datas,
    hiddenimports=[
        "engine",
        "tray",
        "cookies",
        "potoken",
        "sharing",
        "convert",
        "watch",
        # Imported inside the function that uses it, so the analyser never
        # sees it. Leaving it out builds cleanly and fails only on the day the
        # engine is refused - which is the one day it has to work.
        "doors",
        # Reached only through sharing.py, so PyInstaller cannot see them:
        # segno draws the pairing QR, and AES-GCM seals every message the
        # relay carries. Without these Sharing cannot be turned on at all.
        "segno",
        "cryptography.hazmat.primitives.ciphers.aead",
        "clr_loader",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "pystray._win32",
    ] + webview_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Riplox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX packing is itself an antivirus red flag
    console=False,      # no console window behind the app
    disable_windowed_traceback=False,
    icon=str(SRC / "static" / "img" / "riplox.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
)

# The native messaging host: a second, tiny program in the same folder.
#
# It has to be its own executable because Chrome talks to it over stdin and
# stdout, and Riplox itself is built windowed - a windowed process has no
# console streams to talk over. It shares this build's runtime rather than
# carrying a second copy of Python: exclude_binaries keeps it dependent on the
# _internal folder both exes sit beside, so it costs about a megabyte.
host_a = Analysis(
    [str(SRC / "native_host.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

host_pyz = PYZ(host_a.pure)

host_exe = EXE(
    host_pyz,
    host_a.scripts,
    [],
    exclude_binaries=True,
    name="RiploxHost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,       # required: Chrome speaks to it over stdin/stdout
    hide_console="hide-early",   # ...but nobody should ever see the window
    disable_windowed_traceback=False,
    icon=str(SRC / "static" / "img" / "riplox.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    host_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Riplox",
)
