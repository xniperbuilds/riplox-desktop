"""
Riplox Send for Windows - the pairing, and nothing else.

A room id, the key this machine made for itself, and the PC's address on the
local network when it is known. That is the whole of what this app stores:
there is no history, no queue, and no copy of anything that was sent.

It is written through DPAPI, the same way Riplox Desktop protects a captured
browser session, so the file is useless if it is copied to another machine or
read by another user account.
"""

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

APP_NAME = "RiploxSend"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_file() -> Path:
    return data_dir() / "pairing.dat"


# --------------------------------------------------------------------------
# DPAPI
# --------------------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        return data
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    out = _Blob()
    if not fn(ctypes.byref(_blob(data)), None, None, None, None, 0,
              ctypes.byref(out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# --------------------------------------------------------------------------
# Reading and writing
# --------------------------------------------------------------------------

def load() -> dict:
    try:
        raw = _crypt(store_file().read_bytes(), False)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    raw = json.dumps(data).encode("utf-8")
    tmp = store_file().with_suffix(".tmp")
    tmp.write_bytes(_crypt(raw, True))
    tmp.replace(store_file())          # replaced whole, never half-written


def paired() -> bool:
    data = load()
    return bool(data.get("room")) and bool(data.get("key"))


def forget() -> None:
    try:
        store_file().unlink()
    except OSError:
        pass
