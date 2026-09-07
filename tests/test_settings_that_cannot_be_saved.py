"""A settings file that will not be written, and what the user is told about it.

save_settings() replaces the file, and a replace can be refused. Its own retry
loop names the case: "a virus scanner holding the file open turns the rename
into 'Access is denied' and the save is lost." After five tries it raises - and
the raise reached the window as a 500, which app.js turns into

    "The app lost contact with its engine."

The engine answered. It could not write. Telling somebody the wrong thing about
their own machine is the fault this audit spent the most time on, so every
route that saves a setting now says what actually happened.

Reachable, not contrived: a settings.json restored from a backup with the
read-only attribute, one held open by a sync client, one quarantined by an
antivirus, or a portable copy on a write-protected stick - a state the app
already knows about, because portable_state() has a "read-only" value.

    python tests/test_settings_that_cannot_be_saved.py
"""
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-ro-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

BODY = dict(engine.DEFAULT_SETTINGS)
BODY.update({"potoken": True, "fragments": 16, "first_run_done": True,
             "sharing": False, "watch": False, "drop_on": False,
             "engine_auto": False, "check_updates": False,
             "download_dir": str(SANDBOX / "dl")})
BODY.pop("settings_offer_done", None)
(SANDBOX / "dl").mkdir(parents=True, exist_ok=True)


def lay_down():
    if engine.settings_file().exists():
        os.chmod(engine.settings_file(), stat.S_IWRITE)
    with open(engine.settings_file(), "w", encoding="utf-8") as fh:
        json.dump(BODY, fh)


lay_down()

import app                                                  # noqa: E402

PASS, FAIL = [], []
CLIENT = app.app.test_client()
HEAD = {"X-Riplox-Token": app.TOKEN}


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


def post(path, payload=None):
    got = CLIENT.post(path, json=payload or {}, headers=HEAD)
    try:
        return got.status_code, got.get_json()
    except Exception:                                       # noqa: BLE001
        return got.status_code, None


WRITES = ("/api/settings", "/api/settings/take-new", "/api/settings/keep-old",
          "/api/potoken/remove")

print("-- while the file can be written " + "-" * 36)

lay_down()
code, said = post("/api/settings", {"fragments": 8})
check("a save answers 200 and ok", (code, said.get("ok")) == (200, True), code)
check("and it really saved", engine.load_settings()["fragments"] == 8,
      engine.load_settings()["fragments"])

print("\n-- and when it cannot " + "-" * 47)

for path in WRITES:
    lay_down()
    os.chmod(engine.settings_file(), stat.S_IREAD)
    try:
        code, said = post(path, {"fragments": 8})
    finally:
        os.chmod(engine.settings_file(), stat.S_IWRITE)

    check("%-26s does not answer 5xx" % path, code < 500, code)
    check("%-26s says it did not work" % path,
          isinstance(said, dict) and said.get("ok") is False, said)
    words = (said or {}).get("error", "")
    check("%-26s names the settings file" % path, "settings file" in words,
          words[:58])
    check("%-26s does not blame the engine" % path,
          "lost contact" not in words and "engine" not in words.lower(),
          "the engine answered - it could not write")
    check("%-26s says nothing was changed" % path,
          "Nothing was changed" in words)
    check("%-26s left the file alone" % path,
          engine.load_settings()["fragments"] == 16,
          engine.load_settings()["fragments"])

print("\n-- turning Sharing on is not applied when it could not be saved " + "-" * 5)

lay_down()
before = getattr(app.sharing, "_started", None)
os.chmod(engine.settings_file(), stat.S_IREAD)
try:
    code, said = post("/api/settings", {"sharing": True})
finally:
    os.chmod(engine.settings_file(), stat.S_IWRITE)
check("the switch was refused", said.get("ok") is False, said)
check("and sharing was not started behind it",
      getattr(app.sharing, "_started", None) == before,
      "a run doing one thing while the file says another undoes itself "
      "at the next launch")
check("the file still says off", engine.load_settings()["sharing"] is False)

print("\n-- and the helper is not removed on a switch that could not be saved " + "-" * 1)

lay_down()
engine.bin_dir().mkdir(parents=True, exist_ok=True)
import potoken                                              # noqa: E402
potoken.server_path().write_bytes(b"not really an exe")
(potoken.plugin_home() / "yt_dlp_plugins").mkdir(parents=True, exist_ok=True)
check("the helper is installed to begin with", potoken.installed() is True)
os.chmod(engine.settings_file(), stat.S_IREAD)
try:
    code, said = post("/api/potoken/remove")
finally:
    os.chmod(engine.settings_file(), stat.S_IWRITE)
check("removal was refused", said.get("ok") is False, said)
check("and the files are still there", potoken.installed() is True,
      "removing it while the switch still reads on is the state that made "
      "the toggle a lie")

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
if engine.settings_file().exists():
    os.chmod(engine.settings_file(), stat.S_IWRITE)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
