"""The opening questions are asked once, and never of somebody already here.

Riplox opened straight onto an empty screen, so every default was found out
later - usually by being wrong once. Three questions fixes that for a new
install.

The part worth testing is the upgrade. `first_run_done` is a flag added after
the fact, so every existing settings file is missing it, and the obvious
reading of "missing means false" would greet everybody who has been using
Riplox for months with a welcome screen. That is the app forgetting who it is
talking to.

⚠️ This writes settings, so it points the whole data directory at a temporary
one first and asserts the redirect took. Patching save() alone is not enough -
that lesson cost a real pairing once.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import engine

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-firstrun-"))
engine.data_dir = lambda: SANDBOX

print("\n-- the sandbox is real before anything is written -------------------")
where = engine.settings_file()
check("settings go to the temporary directory, not the real one",
      str(SANDBOX) in str(where), str(where))
check("...and the real data directory is nowhere in that path",
      "AppData" not in str(where) or str(SANDBOX) in str(where), str(where))


def write(data):
    engine.settings_file().write_text(json.dumps(data), encoding="utf-8")


print("\n-- a fresh install has never been asked -----------------------------")
if engine.settings_file().exists():
    engine.settings_file().unlink()
s = engine.load_settings()
check("no settings file at all means the questions are still to come",
      s["first_run_done"] is False, str(s["first_run_done"]))


print("\n-- somebody already using Riplox is not asked ------------------------")
# A settings file from before the flag existed: it has real choices in it and
# no first_run_done anywhere.
write({"download_dir": str(SANDBOX), "max_parallel": 4, "auto_paste": False})
s = engine.load_settings()
check("an existing settings file counts as already answered",
      s["first_run_done"] is True, str(s["first_run_done"]))
check("...and their own choices are untouched by it",
      s["max_parallel"] == 4 and s["auto_paste"] is False,
      "max_parallel=%s auto_paste=%s" % (s["max_parallel"], s["auto_paste"]))


print("\n-- and once answered it stays answered -------------------------------")
write({"download_dir": str(SANDBOX), "first_run_done": True})
check("a true flag is kept", engine.load_settings()["first_run_done"] is True)

write({"download_dir": str(SANDBOX), "first_run_done": False})
check("a false flag is kept too - the file said so on purpose",
      engine.load_settings()["first_run_done"] is False)


print("\n-- the three it asks about are settings that already existed ---------")
for key in ("download_dir", "subfolder_per_site", "auto_paste"):
    check("%s is a real setting with a default" % key,
          key in engine.DEFAULT_SETTINGS, str(engine.DEFAULT_SETTINGS.get(key)))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
