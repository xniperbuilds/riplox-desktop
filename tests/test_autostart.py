"""Start with Windows: a development run must refuse, and must leave the
registry exactly as it found it.

The earlier version of this test asserted the registry entry was absent
afterwards. That is only true on a machine where Riplox is not set to start
with Windows - and it reported FAIL on one where the user had turned the
setting on through the installed app, which is not a defect at all. What
matters is whether a dev run CHANGED anything, so that is what is compared.
"""
import sys
from pathlib import Path

import winreg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import engine

RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
fails = []


def check(label, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def entry():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN) as key:
            return winreg.QueryValueEx(key, "Riplox")[0]
    except OSError:
        return None


before = entry()
print(f"registry before: {before!r}  (set by the installed app, if anything)")

answer = engine.set_autostart(True)
check("a dev run refuses to write", answer.get("ok") is False, str(answer))
check("it says why in a sentence a person can read",
      "installed" in (answer.get("message") or "").lower(), answer.get("message"))
check("the registry is exactly as it was", entry() == before,
      f"now {entry()!r}")

off = engine.set_autostart(False)
check("turning it off from a dev run also refuses", off.get("ok") is False)
check("the registry is still exactly as it was", entry() == before,
      f"now {entry()!r}")

check("autostart_on() agrees with the registry",
      engine.autostart_on() == (before is not None),
      f"reads {engine.autostart_on()}, registry has {before is not None}")

command = engine._autostart_command()
check("the command it would write is quoted and asks for the tray",
      command.startswith('"') and command.endswith("--tray"), command)

print(f"\n{len(fails)} failed" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
