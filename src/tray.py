"""
Riplox Desktop - tray icon, notifications and taskbar progress.

The window spends most of its life minimised or behind a browser, so a
download started by the global shortcut has to announce itself somewhere the
user is actually looking: the notification area and the taskbar button.
"""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

import engine

try:
    import pystray
    from PIL import Image
except ImportError:  # tray is a nicety, never a requirement
    pystray = None
    Image = None


# --------------------------------------------------------------------------
# Taskbar progress (the bar that fills the taskbar button, like a download
# manager). Pure ctypes COM so it costs no dependency.
# --------------------------------------------------------------------------

TBPF_NOPROGRESS = 0
TBPF_NORMAL = 2
TBPF_ERROR = 4


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(d1, d2, d3, rest):
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*rest))


CLSID_TASKBAR = _guid(0x56FDF344, 0xFD6D, 0x11D0,
                      (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
IID_TASKBARLIST3 = _guid(0xEA1AFB91, 0x9E28, 0x4B86,
                         (0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF))

CLSCTX_INPROC_SERVER = 1

# Vtable slots: IUnknown 0-2, ITaskbarList 3-7, ITaskbarList2 8,
# then SetProgressValue and SetProgressState.
SLOT_HRINIT = 3
SLOT_SET_VALUE = 9
SLOT_SET_STATE = 10


class TaskbarProgress:
    """Drives the progress overlay on our own taskbar button."""

    def __init__(self, window_title="Riplox"):
        self.title = window_title
        self._taskbar = None
        self._hwnd = None
        self._dead = os.name != "nt"

    def _hwnd_for_window(self):
        if self._hwnd:
            return self._hwnd
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        self._hwnd = user32.FindWindowW(None, self.title)
        return self._hwnd

    def _ensure(self):
        """Create the COM object once, on the calling thread."""
        if self._dead or self._taskbar is not None:
            return self._taskbar

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)

        pointer = ctypes.c_void_p()
        hresult = ole32.CoCreateInstance(
            ctypes.byref(CLSID_TASKBAR), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(IID_TASKBARLIST3), ctypes.byref(pointer))
        if hresult != 0 or not pointer:
            self._dead = True
            return None

        self._taskbar = pointer
        self._invoke(SLOT_HRINIT, [])
        return self._taskbar

    def set(self, percent, failed=False):
        """percent None clears the bar. Never raises - this is decoration."""
        if self._dead:
            return
        try:
            if self._ensure() is None:
                return
            hwnd = self._hwnd_for_window()
            if not hwnd:
                return

            if percent is None:
                self._invoke(SLOT_SET_STATE,
                             [(wintypes.HWND, hwnd), (ctypes.c_int, TBPF_NOPROGRESS)])
                return

            state = TBPF_ERROR if failed else TBPF_NORMAL
            self._invoke(SLOT_SET_STATE,
                         [(wintypes.HWND, hwnd), (ctypes.c_int, state)])
            self._invoke(SLOT_SET_VALUE,
                         [(wintypes.HWND, hwnd),
                          (ctypes.c_ulonglong, int(max(0, min(100, percent)))),
                          (ctypes.c_ulonglong, 100)])
        except Exception:
            self._dead = True

    def _invoke(self, slot, params):
        vtable = ctypes.cast(self._taskbar,
                             ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long,
                                       ctypes.c_void_p, *[t for t, _ in params])
        function = prototype(vtable[0][slot])
        return function(self._taskbar, *[v for _, v in params])


# --------------------------------------------------------------------------
# Tray icon + notifications
# --------------------------------------------------------------------------

# The shell reports a click on the notification itself as its own message.
# pystray only looks for clicks on the icon, so without this a notification is
# something you can read but not act on.
NIN_BALLOONUSERCLICK = 0x0405       # WM_USER + 5


class Tray:
    def __init__(self, manager, icon_path: Path, on_show=None, on_quit=None):
        self.manager = manager
        self.icon_path = Path(icon_path)
        self.on_show = on_show
        self.on_quit = on_quit

        self.icon = None
        self.progress = TaskbarProgress()
        self._seen = {}          # job id -> last status we reported
        self._running = True

    # -- lifecycle -------------------------------------------------------

    def start(self):
        if pystray is not None and self.icon_path.exists():
            threading.Thread(target=self._run_icon, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _run_icon(self):
        try:
            image = Image.open(self.icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("Open Riplox", self._show, default=True),
                pystray.MenuItem("Open download folder", self._open_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit),
            )
            self.icon = pystray.Icon("riplox", image, "Riplox", menu)
            self._hook_notification_click()
            self.icon.run()
        except Exception:
            self.icon = None

    def _hook_notification_click(self):
        """Make clicking a notification open the window."""
        try:
            handlers = self.icon._message_handlers
            for message, handler in list(handlers.items()):
                if handler == self.icon._on_notify:
                    handlers[message] = self._notification_handler(handler)
                    break
        except Exception:
            pass        # a newer pystray may not look like this; not fatal

    def _notification_handler(self, original):
        def handle(wparam, lparam):
            if lparam == NIN_BALLOONUSERCLICK:
                self._show()
                return None
            return original(wparam, lparam)
        return handle

    def stop(self):
        self._running = False
        self.progress.set(None)
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass

    # -- menu actions ----------------------------------------------------

    def _show(self, *_):
        if self.on_show:
            self.on_show()

    def _open_folder(self, *_):
        try:
            folder = Path(engine.load_settings()["download_dir"])
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # noqa: S606
        except OSError:
            pass

    def _quit(self, *_):
        self.stop()
        if self.on_quit:
            self.on_quit()

    # -- notifications ---------------------------------------------------

    def notify(self, title, message):
        if not self.icon:
            return
        try:
            self.icon.notify(str(message)[:180], str(title)[:60])
        except Exception:
            pass

    def _watch(self):
        """One place that turns queue changes into things the user can see."""
        while self._running:
            try:
                self._tick()
            except Exception:
                pass
            time.sleep(0.8)

    def _tick(self):
        jobs = self.manager.snapshot()

        finished, failed = [], []
        live = set()
        for job in jobs:
            live.add(job["id"])
            previous = self._seen.get(job["id"])
            if previous != job["status"]:
                if job["status"] == "done" and previous is not None:
                    finished.append(job)
                elif job["status"] == "error" and previous is not None:
                    failed.append(job)
                self._seen[job["id"]] = job["status"]

        for job_id in list(self._seen):
            if job_id not in live:
                del self._seen[job_id]

        if len(finished) == 1:
            self.notify("Download finished", finished[0]["title"])
        elif len(finished) > 1:
            self.notify("Downloads finished", f"{len(finished)} files saved")

        for job in failed[:2]:
            self.notify("Download failed", job["error"] or job["title"])

        self._update_progress(jobs)

    def _update_progress(self, jobs):
        active = [j for j in jobs
                  if j["status"] in ("downloading", "converting", "starting")]

        if not active:
            queued = any(j["status"] == "queued" for j in jobs)
            self.progress.set(None if not queued else 0)
            self._set_tooltip("Riplox")
            return

        average = sum(j["percent"] for j in active) / len(active)
        self.progress.set(average)

        if len(active) == 1:
            self._set_tooltip(f"Riplox - {average:.0f}%  {active[0]['title'][:40]}")
        else:
            self._set_tooltip(f"Riplox - {len(active)} downloads, {average:.0f}%")

    def _set_tooltip(self, text):
        if self.icon:
            try:
                self.icon.title = text
            except Exception:
                pass
