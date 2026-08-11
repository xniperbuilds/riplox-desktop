# PyInstaller spec for Riplox Send.
#
# onedir and no UPX, for the same reason Riplox Desktop uses them: a one-file
# build unpacks itself into temp on every launch, and UPX-packed executables
# are exactly the shape antivirus heuristics punish.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

webview_datas, webview_binaries, webview_hidden = collect_all("webview")

datas = [(str(SRC / "ui"), "ui")] + webview_datas

a = Analysis(
    [str(SRC / "app.py")],
    pathex=[str(SRC)],
    binaries=webview_binaries,
    datas=datas,
    hiddenimports=[
        "send",
        "store",
        # pystray picks its backend at runtime, so the Windows one has to be
        # named or it is simply not in the build.
        "pystray._win32",
        "cryptography.hazmat.primitives.ciphers.aead",
    ] + webview_hidden,
    hookspath=[],
    runtime_hooks=[],
    # A sender does not do maths. Left in, numpy alone is tens of megabytes -
    # and a stale copy of it has boot-crashed a build of ours before.
    excludes=["numpy", "scipy", "pandas", "matplotlib", "tkinter", "test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RiploxSend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(SRC / "ui" / "riploxsend.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RiploxSend",
)
