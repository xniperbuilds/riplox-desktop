"""A shortcut the user chooses, and every way of getting it wrong.

Riplox used to try four fixed combinations and take the first Windows granted.
When all four were spoken for there was no shortcut and nothing the user could
do - which is the dead end this feature exists to close.

⚠️ The letter in a combination comes from the PHYSICAL key. The browser sends
event.code, never event.key: RegisterHotKey wants a virtual-key code, and that
follows the physical key. On a French or German layout the character produced
by the same key is a different letter, so a shortcut built from the character
would register a key nobody pressed. That half cannot be tested from Python -
it is checked by hand - but everything on this side can be.

LOCALAPPDATA is redirected before the app is imported, so nothing here touches
real settings.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-hotkey-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app as riplox                                          # noqa: E402
import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


CTRL = riplox.MOD_CONTROL
ALT = riplox.MOD_ALT
SHIFT = riplox.MOD_SHIFT


print("\n-- combinations that are allowed ------------------------------------")
for text, mods, vk, label in [
    ("Ctrl+Shift+D", CTRL | SHIFT, 0x44, "Ctrl + Shift + D"),
    ("Ctrl+D", CTRL, 0x44, "Ctrl + D"),                    # two keys is fine
    ("Alt+Q", ALT, 0x51, "Alt + Q"),
    ("Ctrl+Alt+Shift+K", CTRL | ALT | SHIFT, 0x4B, "Ctrl + Alt + Shift + K"),
    ("Ctrl+5", CTRL, 0x35, "Ctrl + 5"),
    ("Ctrl+F1", CTRL, 0x70, "Ctrl + F1"),
    ("Ctrl+F24", CTRL, 0x87, "Ctrl + F24"),
    ("ctrl+shift+d", CTRL | SHIFT, 0x44, "Ctrl + Shift + D"),   # any case
    ("  Ctrl + Shift + D  ", CTRL | SHIFT, 0x44, "Ctrl + Shift + D"),
]:
    got_mods, got_vk, got_label = riplox.parse_combo(text)
    ok = got_mods == mods and got_vk == vk and got_label == label
    check("%-22s -> %s" % (text.strip(), label), ok,
          "got %s / %s / %s" % (got_mods, hex(got_vk) if got_vk else None, got_label))


print("\n-- and the ones that must be refused --------------------------------")
for text, expect in [
    ("D", "Ctrl, Alt or Shift"),
    ("F5", "Ctrl, Alt or Shift"),
    ("Ctrl+Shift", "letter"),
    ("Ctrl", "letter"),
    ("", "press"),
    ("Ctrl+Shift+D+K", "one key"),
    ("Ctrl+Enter", "A-Z"),
    ("Ctrl+F25", "A-Z"),
    ("Ctrl+Ø", "A-Z"),
]:
    mods, vk, why = riplox.parse_combo(text)
    refused = mods is None and vk is None
    said = expect.lower() in (why or "").lower()
    check("%-16s refused" % (repr(text)), refused and said, why)

check("⭐ a bare key is refused for the right reason - it would take that key "
      "from every other program",
      "every other program" in (riplox.parse_combo("D")[2] or ""))


print("\n-- the virtual-key codes are the Windows ones -----------------------")
# Wrong values here would register a different key than the one displayed,
# which is the whole failure this table exists to avoid.
check("A is VK_A (0x41)", riplox._VK_NAMES["A"] == 0x41)
check("Z is VK_Z (0x5A)", riplox._VK_NAMES["Z"] == 0x5A)
check("0 is VK_0 (0x30)", riplox._VK_NAMES["0"] == 0x30)
check("9 is VK_9 (0x39)", riplox._VK_NAMES["9"] == 0x39)
check("F1 is VK_F1 (0x70)", riplox._VK_NAMES["F1"] == 0x70)
check("F24 is VK_F24 (0x87)", riplox._VK_NAMES["F24"] == 0x87)
check("nothing outside A-Z, 0-9, F1-F24 is offered",
      len(riplox._VK_NAMES) == 26 + 10 + 24, len(riplox._VK_NAMES))


print("\n-- asking Windows whether it is free --------------------------------")
# Take a combination and hand it straight back. Doing it twice must give the
# same answer: a check that leaked the registration would say "free" once and
# "taken" ever after.
first = riplox.combo_free(CTRL | ALT | SHIFT, 0x87)          # Ctrl+Alt+Shift+F24
second = riplox.combo_free(CTRL | ALT | SHIFT, 0x87)
check("⭐ the check gives the same answer twice - it does not keep what it took",
      first == second, "%s then %s" % (first, second))
check("the test id is not the one the shortcut itself uses",
      riplox.HOTKEY_TEST_ID != riplox.HOTKEY_ID)


print("\n-- the setting exists and defaults to automatic ---------------------")
check("hotkey_combo is a known setting, so save_settings will keep it",
      "hotkey_combo" in engine.DEFAULT_SETTINGS)
check("⭐ empty by default - anyone who never opens this keeps the old behaviour",
      engine.DEFAULT_SETTINGS["hotkey_combo"] == "")

shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
