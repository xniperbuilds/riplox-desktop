"""
Riplox Desktop - YouTube proof-of-origin token helper.

YouTube expects most clients to present a proof-of-origin token. Without one,
requests look automated and are met with "Sign in to confirm you're not a bot"
even when nothing is wrong with the account or the video.

yt-dlp can fetch such a token from a local provider. The provider used here is
a single native executable, so no Node or Deno runtime has to be installed.

Three rules this module keeps:

* It is opt-in and off by default. Riplox is a clean installer and it stays
  one; a 44 MB third-party binary is never fetched behind the user's back.
* Whatever is downloaded is checked against a pinned SHA-256 before it is ever
  run.
* The server is a child process, and a child process that outlives its parent
  is a bug. It is tracked, stopped on exit, and any orphan is cleaned up on the
  next start.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import engine

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Pinned. Upgrading means changing the tag and both digests together, never
# one of them.
RELEASE = "v0.8.1"
BASE_URL = ("https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs/"
            f"releases/download/{RELEASE}/")

SERVER_NAME = "bgutil-pot.exe"
SERVER_ASSET = "bgutil-pot-windows-x86_64.exe"
SERVER_SHA = "25d6b05c79176aa792454c3d1727922ca47e56cf11cb1e866615d751819b14a0"

PLUGIN_ASSET = "bgutil-ytdlp-pot-provider-rs.zip"
PLUGIN_SHA = "99fd83b98fa93b193d6a3b69dc74410d76e7a2b889868c54d16121cac9060344"

DOWNLOAD_MB = 44


def server_path() -> Path:
    return engine.bin_dir() / SERVER_NAME


def plugin_dir() -> Path:
    """
    What gets passed to --plugin-dirs.

    yt-dlp lists the children of this folder and expects each one to contain a
    yt_dlp_plugins package - so the archive goes one level down, not here.
    Extracting straight into this folder gives "Plugin directories: none".
    """
    return engine.bin_dir() / "yt-dlp-plugins"


def plugin_home() -> Path:
    return plugin_dir() / "bgutil"


def marker_file() -> Path:
    return engine.data_dir() / "potoken.json"


def installed() -> bool:
    return (server_path().exists()
            and (plugin_home() / "yt_dlp_plugins").is_dir())


# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------

class _State:
    def __init__(self):
        self.busy = False
        self.percent = 0
        self.message = ""
        self.error = ""
        self.proc = None
        self.port = 0
        self.lock = threading.Lock()


_state = _State()


def _download(url: str, expect_sha: str, progress=None) -> bytes:
    """Fetch and verify. A binary that fails its digest is never written."""
    digest = hashlib.sha256()
    chunks = []
    with urllib.request.urlopen(url, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
            got += len(chunk)
            if progress and total:
                progress(int(got * 100 / total))

    if digest.hexdigest() != expect_sha:
        raise OSError("The download did not match its checksum and was discarded.")
    return b"".join(chunks)


def install() -> dict:
    """Start the download on a background thread. Poll status() for progress."""
    with _state.lock:
        if _state.busy:
            return {"ok": False, "error": "Already downloading."}
        if installed():
            return {"ok": True, "already": True}
        _state.busy = True
        _state.percent = 0
        _state.error = ""
        _state.message = "Starting..."

    def work():
        try:
            _state.message = f"Downloading helper ({DOWNLOAD_MB} MB)"
            body = _download(BASE_URL + SERVER_ASSET, SERVER_SHA,
                             lambda p: setattr(_state, "percent", p))
            engine.bin_dir().mkdir(parents=True, exist_ok=True)
            tmp = server_path().with_suffix(".part")
            tmp.write_bytes(body)
            tmp.replace(server_path())

            _state.message = "Downloading plugin"
            plugin = _download(BASE_URL + PLUGIN_ASSET, PLUGIN_SHA)
            target = plugin_home()
            shutil.rmtree(target, ignore_errors=True)   # no stale mix of versions
            target.mkdir(parents=True, exist_ok=True)
            zip_tmp = engine.bin_dir() / "_plugin.zip"
            zip_tmp.write_bytes(plugin)
            try:
                with zipfile.ZipFile(zip_tmp) as archive:
                    _safe_extract(archive, target)
            finally:
                try:
                    zip_tmp.unlink()
                except OSError:
                    pass

            marker_file().write_text(json.dumps({"release": RELEASE}), encoding="utf-8")
            _state.percent = 100
            _state.message = "Ready"
        except (OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
            _state.error = str(exc)[:200]
            _state.message = ""
        finally:
            _state.busy = False

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    """Never let an archive write outside the folder it was aimed at."""
    root = target.resolve()
    for member in archive.infolist():
        destination = (root / member.filename).resolve()
        if root not in destination.parents and destination != root:
            raise OSError("The plugin archive contained an unexpected path.")
    archive.extractall(root)


def remove() -> dict:
    stop()
    for path in (server_path(), marker_file()):
        try:
            path.unlink()
        except OSError:
            pass
    shutil.rmtree(plugin_dir(), ignore_errors=True)
    return {"ok": True}


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def kill_orphans() -> None:
    """
    A previous run that crashed can leave the server behind. Riplox has been
    bitten by duplicate processes before, so this runs at every startup.
    """
    if os.name != "nt":
        return
    try:
        subprocess.run(["taskkill", "/IM", SERVER_NAME, "/F"],
                       capture_output=True, timeout=15, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def ensure_running() -> str:
    """
    Return the base URL of a live provider, or "" if there is none. Never
    raises: a download must still work when the helper does not.
    """
    if not installed():
        return ""

    with _state.lock:
        if _state.proc is not None and _state.proc.poll() is None and _alive(_state.port):
            return f"http://127.0.0.1:{_state.port}"

        port = _free_port()
        try:
            proc = subprocess.Popen(
                [str(server_path()), "server", "--host", "127.0.0.1",
                 "--port", str(port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
        except OSError:
            return ""

        # Even a hard kill of Riplox now takes the helper with it.
        engine.tie_to_app(proc)

        deadline = time.time() + 12
        while time.time() < deadline:
            if proc.poll() is not None:
                return ""
            if _alive(port):
                _state.proc = proc
                _state.port = port
                return f"http://127.0.0.1:{port}"
            time.sleep(0.3)

        # Started but never listened. Do not leave it running.
        try:
            proc.kill()
        except OSError:
            pass
        return ""


def stop() -> None:
    proc = _state.proc
    _state.proc = None
    _state.port = 0
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def status() -> dict:
    return {
        "installed": installed(),
        "running": _state.proc is not None and _state.proc.poll() is None,
        "busy": _state.busy,
        "percent": _state.percent,
        "message": _state.message,
        "error": _state.error,
        "sizeMb": DOWNLOAD_MB,
        "release": RELEASE,
        "source": BASE_URL + SERVER_ASSET,
    }
