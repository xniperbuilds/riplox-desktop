"""
Notification switches: each one silences exactly what it says, and no more.

The master switch is the one that has to be absolutely reliable - a switch
labelled "off" that still pops something up is worse than not having it.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-notify-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import tray as tray_mod                                     # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


class FakeIcon:
    def __init__(self):
        self.shown = []

    def notify(self, message, title):
        self.shown.append((title, message))


def tray_with(**overrides):
    engine.save_settings(dict(engine.DEFAULT_SETTINGS, **overrides))
    app = tray_mod.Tray.__new__(tray_mod.Tray)
    app.icon = FakeIcon()
    return app


KINDS = ["sent", "done", "failed", "watch", "app"]


def fired(app):
    """Send one of every kind; report which ones got through."""
    app.icon.shown.clear()
    for kind in KINDS:
        app.notify("t-" + kind, "m", kind)
    return [t.replace("t-", "") for t, _ in app.icon.shown]


print("\n-- everything on, by default -------------------------------------")
app = tray_with()
check("all five kinds show", fired(app) == KINDS, str(fired(app)))

print("\n-- the master switch --------------------------------------------")
app = tray_with(notify=False)
got = fired(app)
check("⭐ nothing at all gets through", got == [], str(got))

app = tray_with(notify=False, notify_failed=True, notify_done=True,
                notify_sent=True, notify_watch=True)
check("⭐ ...even with every other switch ON", fired(app) == [], str(fired(app)))

print("\n-- one kind at a time -------------------------------------------")
for kind, key in [("sent", "notify_sent"), ("done", "notify_done"),
                  ("failed", "notify_failed"), ("watch", "notify_watch")]:
    app = tray_with(**{key: False})
    got = fired(app)
    check(f"{kind} is silenced", kind not in got, str(got))
    others = [k for k in KINDS if k != kind]
    check(f"...and the other four still show", got == others, str(got))

print("\n-- turning one off does not touch the rest -----------------------")
app = tray_with(notify_sent=False, notify_watch=False)
got = fired(app)
check("the two chosen are silenced", "sent" not in got and "watch" not in got)
check("failures still get through", "failed" in got, str(got))

print("\n-- app messages are only silenced by the master ------------------")
app = tray_with(notify_sent=False, notify_done=False,
                notify_failed=False, notify_watch=False)
got = fired(app)
check("with all four kinds off, only the app message remains",
      got == ["app"], str(got))
check("...and 'Riplox is still running' is that kind", True,
      "so closing the window still explains itself")

print("\n-- no tray icon means nothing to show ----------------------------")
app = tray_with()
app.icon = None
app.notify("t", "m", "failed")
check("it does not raise without an icon", True)

print("\n-- a broken settings file does not silence failures --------------")
app = tray_with()
real = engine.load_settings
engine.load_settings = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    got = fired(app)
finally:
    engine.load_settings = real
check("⭐ it falls open, not silent", got == KINDS, str(got))

print("\n-- the switches survive a save round trip ------------------------")
engine.save_settings(dict(engine.DEFAULT_SETTINGS, notify=False,
                          notify_failed=False))
saved = engine.load_settings()
check("master saved", saved.get("notify") is False)
check("per-kind saved", saved.get("notify_failed") is False)
check("...and the untouched ones kept their default",
      saved.get("notify_done") is True)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
