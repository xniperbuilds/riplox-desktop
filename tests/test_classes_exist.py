"""
Every class the script uses, the stylesheet knows about.

`test_ids_exist.py` guards one third of the join between the interface and the
behaviour. This guards the other two, and it exists because the 1.5 redesign
audit found what happens without it.

  1. The script writes 13 state classes across 56 sites - `on`, `is-active`,
     `is-hidden`, `is-queued` and so on. `classList.add("is-active")` on an
     element whose stylesheet has never heard of `is-active` **succeeds**. It
     returns nothing. Nothing throws. The app renders correctly and then fails
     to respond to being used - tabs not highlighting, rows not marking
     themselves - with no error anywhere.

  2. 137 opening tags and 62 distinct classes of markup are authored inside
     app.js: queue rows, library rows, the format table, the chapter list.
     Rebuilding index.html does not touch a single one of them, so a redesign
     can leave every generated row wearing the old look while the page around
     it is new.

Both are silent. That is the whole reason for the file.
"""

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
JS = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
CSS = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


# Every class name the stylesheet mentions anywhere in a selector.
styled = set(re.findall(r"\.([a-zA-Z][\w-]*)", CSS))

# Class names that are deliberately not styled: they carry meaning for the
# script alone. Named here rather than left to a blanket exception, so the
# list stays short and visible.
SCRIPT_ONLY = set()


print("\n-- the state classes the script writes ----------------------------")
state = sorted(set(re.findall(
    r'classList\.(?:add|remove|toggle|contains)\(\s*"([\w-]+)"', JS)))
print(f"      {len(state)} distinct: " + ", ".join(state))

unstyled = [c for c in state if c not in styled and c not in SCRIPT_ONLY]
check("every state class the script writes is defined in the stylesheet",
      not unstyled,
      "not styled: " + ", ".join(unstyled) if unstyled else "")
check("...and there are some, so this check is doing work", len(state) >= 5,
      str(len(state)))


print("\n-- the markup the script builds -----------------------------------")
authored = set()
# Only the literal text before any concatenation. `class="chip' + (n === x ...`
# is one attribute in the source and two different things: a class name and a
# JavaScript variable. Reading the whole attribute reported `clipSeconds` and
# `libSource` as missing styles, which was the test being wrong rather than
# the app.
for chunk in re.findall(r'class=\\?"([^"\'+]*)', JS):
    for n in chunk.split():
        if re.fullmatch(r"[a-z][\w-]*", n):
            authored.add(n)
print(f"      {len(authored)} distinct classes written from JavaScript")

homeless = sorted(c for c in authored if c not in styled and c not in SCRIPT_ONLY)
check("every class the script authors is defined in the stylesheet",
      not homeless,
      "no rule for: " + ", ".join(homeless[:10]) if homeless else "")
check("...and the script really does author markup", len(authored) >= 20,
      str(len(authored)))


print("\n-- what this will catch when the redesign lands --------------------")
# Stated rather than assumed: the redesign replaces app.css. At that moment
# every name above has to exist in the new sheet or be renamed in the script.
# This is the test that makes that a red line instead of a quiet one.
check("the two lists together cover both silent failures",
      len(state) > 0 and len(authored) > 0,
      f"{len(state)} state + {len(authored)} authored")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
