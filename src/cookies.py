"""
Riplox Desktop - browser sign-in and cookie capture.

Chromium browsers have bound their cookie store to themselves since Chrome 127
(App-Bound Encryption), so no other program can decrypt the file. Nothing here
tries to. Instead the browser is asked to hand its own cookies over through the
DevTools protocol, which is the browser's own supported interface.

Two deliberate details:

* Riplox uses its own browser profile, never the user's. Reading the real
  profile is both blocked on the default data directory since Chrome 136 and a
  good way to make YouTube rotate and invalidate the live session.

* Sign-in and extraction are separate launches. Google refuses to sign in when
  the browser was started with a debugging port, so the login window is started
  with no automation switches at all; the port is only opened afterwards, on a
  profile that is already signed in.

Captured cookies are stored encrypted with DPAPI and only written out as plain
text into a short-lived temp file for the moment a download needs them.
"""

import base64
import ctypes
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

import engine

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Only these ever reach a cookie file. A Google session must not be handed to
# the other thousand extractors yt-dlp supports.
AUTH_DOMAINS = {
    "youtube.com": ("youtube.com", "google.com", "googlevideo.com", "ytimg.com"),
}


# --------------------------------------------------------------------------
# Where things live
# --------------------------------------------------------------------------

def profile_dir() -> Path:
    return engine.data_dir() / "browser-profile"


def store_file() -> Path:
    return engine.data_dir() / "cookies.dat"


def temp_dir() -> Path:
    d = engine.data_dir() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# Finding a browser
# --------------------------------------------------------------------------

BROWSERS = [
    ("Chrome", "chrome.exe", r"Google\Chrome\Application\chrome.exe"),
    ("Edge", "msedge.exe", r"Microsoft\Edge\Application\msedge.exe"),
    ("Brave", "brave.exe", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
]


def _from_app_paths(exe_name: str):
    """The registry knows where a browser installed itself."""
    if os.name != "nt":
        return None
    import winreg
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe_name
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as handle:
                path = Path(winreg.QueryValueEx(handle, "")[0])
                if path.exists():
                    return path
        except OSError:
            continue
    return None


def find_browser():
    """(label, exe) for the first Chromium browser on this machine, or None."""
    bases = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
             os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]

    for label, exe_name, suffix in BROWSERS:
        found = _from_app_paths(exe_name)
        if found:
            return label, found
        for base in bases:
            if not base:
                continue
            candidate = Path(base) / suffix
            if candidate.exists():
                return label, candidate
    return None


# --------------------------------------------------------------------------
# DPAPI - encrypt at rest, so a stolen file is useless on another machine
# --------------------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        return data
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    out = _Blob()
    args = [ctypes.byref(_blob(data)), None, None, None, None, 0,
            ctypes.byref(out)]
    if not fn(*args):
        raise OSError("DPAPI call failed")
    try:
        return _blob_bytes(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _save_cookies(cookies: list) -> None:
    raw = json.dumps({"saved": time.time(), "cookies": cookies}).encode("utf-8")
    tmp = store_file().with_suffix(".tmp")
    tmp.write_bytes(_crypt(raw, True))
    tmp.replace(store_file())


def _load_cookies() -> dict:
    try:
        raw = _crypt(store_file().read_bytes(), False)
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return {}


def have_cookies() -> bool:
    return store_file().exists()


def status() -> dict:
    data = _load_cookies()
    found = find_browser()
    return {
        "browser": found[0] if found else "",
        "haveCookies": bool(data.get("cookies")),
        "count": len(data.get("cookies") or []),
        "saved": data.get("saved", 0),
        "sites": sorted({_root_domain(c.get("domain", ""))
                         for c in (data.get("cookies") or [])} - {""}),
        "busy": _flow.busy,
        "step": _flow.step,
        "error": _flow.error,
    }


def forget() -> None:
    """Delete the saved session and the browser profile that produced it."""
    try:
        store_file().unlink()
    except OSError:
        pass
    shutil.rmtree(profile_dir(), ignore_errors=True)


# --------------------------------------------------------------------------
# A very small WebSocket client
# --------------------------------------------------------------------------
# DevTools speaks WebSocket and nothing else. Pulling in a library for one
# request would be a new dependency in a build that has stayed dependency-thin
# on purpose, so this handles the two frame types that actually occur here.

class _Socket:
    def __init__(self, url: str, timeout: float = 20.0):
        parts = urlsplit(url)
        self.sock = socket.create_connection(
            (parts.hostname, parts.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parts.hostname}:{parts.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(1)
            if not chunk:
                raise OSError("DevTools closed the connection")
            header += chunk
        if b" 101 " not in header.split(b"\r\n")[0]:
            raise OSError("DevTools refused the WebSocket upgrade")

    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise OSError("DevTools connection ended")
            out += chunk
        return out

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])          # FIN + text frame
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)    # client frames must be masked
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        """Reassemble one message, which for getCookies spans many frames."""
        message = b""
        while True:
            first, second = self._read(2)
            fin = first & 0x80
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._read(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read(8), "big")
            payload = self._read(length) if length else b""

            if opcode == 0x8:
                raise OSError("DevTools closed the socket")
            if opcode == 0x9:               # ping - answer or it hangs up
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0xA:
                continue

            message += payload
            if fin:
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Driving the browser
# --------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_tree(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.terminate()
        except OSError:
            pass


COMMON_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--no-service-autorun",
]


def _launch_login(exe: Path, url: str):
    """
    Plain window, no automation switches. Google blocks sign-in when it can
    tell the browser is being driven, so this launch must look ordinary.
    """
    args = [str(exe), f"--user-data-dir={profile_dir()}"] + COMMON_FLAGS + [url]
    return subprocess.Popen(args, creationflags=_NO_WINDOW)


def _read_cookies(exe: Path) -> list:
    """Reopen the signed-in profile headless and ask it for its cookies."""
    port = _free_port()
    args = [
        str(exe),
        f"--user-data-dir={profile_dir()}",
        "--headless=new",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
    ] + COMMON_FLAGS + ["about:blank"]

    proc = subprocess.Popen(args, creationflags=_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # A headless browser nobody can see is the worst kind of thing to leave
    # running, so hand it to Windows to clean up if Riplox dies mid-read.
    engine.tie_to_app(proc)
    try:
        ws_url = _wait_for_devtools(port, proc)
        sock = _Socket(ws_url)
        try:
            sock.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            deadline = time.time() + 30
            while time.time() < deadline:
                message = json.loads(sock.recv())
                if message.get("id") != 1:
                    continue
                if "error" in message:
                    raise OSError(message["error"].get("message", "DevTools error"))
                return message.get("result", {}).get("cookies", []) or []
            raise OSError("The browser did not answer in time.")
        finally:
            sock.close()
    finally:
        _kill_tree(proc)


def _wait_for_devtools(port: int, proc) -> str:
    import urllib.request
    deadline = time.time() + 25
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise OSError("The browser closed before it could be read.")
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=3) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            url = info.get("webSocketDebuggerUrl")
            if url:
                return url
        except Exception as exc:            # not up yet is the normal case
            last = str(exc)
        time.sleep(0.4)
    raise OSError("Could not reach the browser's debugging port. " + last)


# --------------------------------------------------------------------------
# The sign-in flow, run on its own thread so the UI can poll it
# --------------------------------------------------------------------------

class _Flow:
    def __init__(self):
        self.busy = False
        self.step = ""
        self.error = ""
        self.proc = None
        self._lock = threading.Lock()

    def start(self, url: str) -> dict:
        found = find_browser()
        if not found:
            return {"ok": False, "error": "No Chrome, Edge or Brave found on this PC."}

        with self._lock:
            if self.busy:
                return {"ok": False, "error": "A sign-in is already open."}
            self.busy = True
            self.step = "opening"
            self.error = ""

        threading.Thread(target=self._run, args=(found[1], url), daemon=True).start()
        return {"ok": True, "browser": found[0]}

    def _run(self, exe: Path, url: str) -> None:
        try:
            profile_dir().mkdir(parents=True, exist_ok=True)
            self.step = "waiting"
            self.proc = _launch_login(exe, url)
            self.proc.wait()
            self.proc = None

            self.step = "reading"
            found = _read_cookies(exe)
            if not found:
                raise OSError("No cookies were found - was the sign-in completed?")
            _save_cookies(found)
            self.step = "done"
        except Exception as exc:
            self.error = str(exc)[:200]
            self.step = "failed"
        finally:
            self.busy = False

    def refresh(self) -> dict:
        """Re-read the existing profile without opening a login window."""
        found = find_browser()
        if not found:
            return {"ok": False, "error": "No Chrome, Edge or Brave found on this PC."}
        if not profile_dir().exists():
            return {"ok": False, "error": "Sign in first."}

        with self._lock:
            if self.busy:
                return {"ok": False, "error": "Busy."}
            self.busy = True
            self.step = "reading"
            self.error = ""

        def work():
            try:
                got = _read_cookies(found[1])
                if not got:
                    raise OSError("The saved sign-in looks empty - sign in again.")
                _save_cookies(got)
                self.step = "done"
            except Exception as exc:
                self.error = str(exc)[:200]
                self.step = "failed"
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def cancel(self) -> None:
        _kill_tree(self.proc)


_flow = _Flow()


def start_login(url: str = "https://www.youtube.com/") -> dict:
    return _flow.start(url)


def refresh() -> dict:
    return _flow.refresh()


def cancel() -> None:
    _flow.cancel()


# --------------------------------------------------------------------------
# Handing cookies to yt-dlp
# --------------------------------------------------------------------------

def _root_domain(host: str) -> str:
    host = (host or "").lstrip(".").lower()
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return host
    # Good enough for the sites this ever sees; a public-suffix list would be
    # a dependency for no practical gain here.
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _wanted_domains(url: str) -> set:
    root = _root_domain(urlsplit(url).hostname or "")
    return set(AUTH_DOMAINS.get(root, (root,)))


def _netscape(cookies: list, domains: set) -> str:
    lines = ["# Netscape HTTP Cookie File",
             "# Written by Riplox. Do not edit.", ""]
    for c in cookies:
        domain = (c.get("domain") or "").strip()
        if not domain or _root_domain(domain) not in domains:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        expires = c.get("expires") or 0
        try:
            expires = int(float(expires))
        except (TypeError, ValueError):
            expires = 0
        expires = max(expires, 0)
        name = c.get("name") or ""
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        lines.append("\t".join([
            prefix + domain,
            include_sub,
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(expires),
            name,
            c.get("value") or "",
        ]))
    return "\n".join(lines) + "\n"


def materialize(url: str):
    """
    Write the cookies this URL is allowed to see into a temp file and return
    its path, or None when there is nothing to give. The caller must always
    call release() afterwards - the file is a live session in the clear.
    """
    data = _load_cookies()
    cookies = data.get("cookies") or []
    if not cookies or not url:
        return None

    domains = _wanted_domains(url)
    body = _netscape(cookies, domains)
    if body.count("\n") <= 3:               # header only, nothing matched
        return None

    path = temp_dir() / f"c{uuid.uuid4().hex}.txt"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return path


def release(path) -> None:
    if not path:
        return
    try:
        Path(path).unlink()
    except OSError:
        pass


def sweep_temp() -> None:
    """Delete anything a crash left behind. Called at startup."""
    try:
        for leftover in temp_dir().glob("c*.txt"):
            try:
                leftover.unlink()
            except OSError:
                pass
    except OSError:
        pass
