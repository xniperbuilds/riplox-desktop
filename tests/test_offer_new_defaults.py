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
    """Write a settings file the way some older Riplox would have.

    ⚠️ The two stale values are now from DIFFERENT versions, and that is the
    whole point of this file. v1.4.1 left the helper off; v1.6.0 left sixteen
    pieces. A copy has one of those to answer for, rarely both.
    """
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

print("\n-- upgrading from v1.4.1: helper off, four pieces " + "-" * 19)
saved()
offer = engine.stale_defaults()
check("the helper is offered", offer.get("potoken") is True, offer)
check("and the pieces are NOT - four is the default again",
      "fragments" not in offer, offer)

print("\n-- a fresh v1.6.0 install: helper on, sixteen pieces " + "-" * 16)
saved(potoken=True, fragments=16)
offer = engine.stale_defaults()
check("the pieces are offered, downwards",
      offer == {"fragments": 4}, offer)

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
saved(potoken=False, fragments=16)          # both stale at once, the rare case
engine.take_new_defaults()
now = engine.load_settings()
check("the helper is on", now.get("potoken") is True, now.get("potoken"))
check("pieces moved to 4", now.get("fragments") == 4, now.get("fragments"))
check("and it is not asked again", engine.stale_defaults() == {})
args = [str(a) for a in engine.extra_args(now, "best")]
check("the engine is told the new number",
      args[args.index("--concurrent-fragments") + 1] == "4")

print("\n-- and closing it instead " + "-" * 43)
saved(potoken=False, fragments=16)
engine.keep_old_defaults()
now = engine.load_settings()
check("nothing was changed", now.get("potoken") is False
      and now.get("fragments") == 16,
      "%s / %s" % (now.get("potoken"), now.get("fragments")))
check("but it is not asked again either", engine.stale_defaults() == {})

print("\n-- and the screen behind it is brought up to date " + "-" * 19)

# 🔴 The Settings screen is drawn by the template, once, at startup, and
# nothing else on the page writes these controls. Without this the screen goes
# on showing what was just replaced - and each control saves ITS OWN value
# when touched, so the next click on either writes the old number back and
# undoes the answer the user gave. Measured in the window before it was fixed:
# the offer moved pieces to 4 and the dropdown still read 16.
JS = (Path(__file__).resolve().parent.parent
      / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8",
                                                      errors="replace")
start = JS.index("function offerNewDefaults()")
offer_js = JS[start:start + 2000]

check("the pieces dropdown is written from the answer",
      "setFragments" in offer_js and "res.settings.fragments" in offer_js)
check("so is the helper toggle",
      "setPotoken" in offer_js and "res.settings.potoken" in offer_js)
check("neither is touched when the answer does not carry it",
      '"potoken" in res.settings' in offer_js
      and "res.settings.fragments)" in offer_js,
      "a partial reply saying the opposite is worse than a stale screen")

# The check that keeps this true: a third key added to the offer, with no
# control written for it, inherits exactly the bug this fixes.
for key in engine._WAS_DEFAULT:
    check("the offer's %r is written back to its control" % key,
          ("res.settings." + key) in offer_js,
          "every key the offer can carry needs a line in offerNewDefaults")

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
