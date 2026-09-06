"""The one-time offer after an upgrade, and the choice it must not overwrite.

Two defaults moved on 3 Sep 2026 - the YouTube helper on, pieces per file to
16 - because a machine hitting the repeated https failures was found with both
in the old state. Changing DEFAULT_SETTINGS reached new installs and nobody
else: load_settings lets a saved value win, and save_settings has always
written the whole dict, so anyone who had ever changed one setting already had
these two written down. The people the change was made for were exactly the
ones it could not reach.

So it is offered. The rule that makes offering safe is the one this file is
mostly about: a value is only offered when it is still sitting on the OLD
default. Somebody who turned the helper off on purpose, or chose eight pieces,
chose that - and quietly putting it back would be the same fault pointing the
other way.

    python tests/test_offer_new_defaults.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-offer-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


def saved(**over):
    """Write a settings file the way an older Riplox would have."""
    body = dict(engine.DEFAULT_SETTINGS)
    body.update({"potoken": False, "fragments": 4})     # what v1.4.1 shipped
    body.pop("settings_offer_done", None)
    body.update(over)
    with open(engine.settings_file(), "w", encoding="utf-8") as fh:
        json.dump(body, fh)


print("-- a fresh install is not asked anything " + "-" * 28)
engine.settings_file().unlink(missing_ok=True)
check("no settings file means no offer", engine.stale_defaults() == {},
      engine.stale_defaults())

print("\n-- an upgrade that never touched either one " + "-" * 25)
saved()
offer = engine.stale_defaults()
check("both are offered", offer == {"potoken": True, "fragments": 16}, offer)

print("\n-- and a choice somebody actually made " + "-" * 30)
saved(potoken=True)
check("a helper turned on is left alone",
      "potoken" not in engine.stale_defaults(), engine.stale_defaults())
saved(fragments=8)
check("eight pieces is a choice, not a stale default",
      "fragments" not in engine.stale_defaults(), engine.stale_defaults())
saved(potoken=True, fragments=8)
check("neither is offered when both were chosen",
      engine.stale_defaults() == {}, engine.stale_defaults())
saved(fragments=8)
check("the other one is still offered on its own",
      engine.stale_defaults() == {"potoken": True}, engine.stale_defaults())

print("\n-- saying yes " + "-" * 55)
saved()
engine.take_new_defaults()
now = engine.load_settings()
check("the helper is on", now.get("potoken") is True, now.get("potoken"))
check("pieces moved to 16", now.get("fragments") == 16, now.get("fragments"))
check("and it is not asked again", engine.stale_defaults() == {})
args = [str(a) for a in engine.extra_args(now, "best")]
check("the engine is told the new number",
      args[args.index("--concurrent-fragments") + 1] == "16")

print("\n-- and closing it instead " + "-" * 43)
saved()
engine.keep_old_defaults()
now = engine.load_settings()
check("nothing was changed", now.get("potoken") is False
      and now.get("fragments") == 4,
      "%s / %s" % (now.get("potoken"), now.get("fragments")))
check("but it is not asked again either", engine.stale_defaults() == {})

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
