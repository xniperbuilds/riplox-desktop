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
# Every stylesheet the page loads, not just the app's own. The design ships as
# tokens.css and components.css and they are linked after app.css, so a class
# defined only there is still defined - reading one file reported `light` as
# unstyled the moment the theme switch started using the design's own class.
CSS = "\n".join(
    (SRC / "static" / "css" / name).read_text(encoding="utf-8")
    for name in ("app.css", "tokens.css", "components.css", "app-shell.css")
    if (SRC / "static" / "css" / name).exists()
)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


NAME = re.compile(r"\.([a-zA-Z][\w-]*)")


def styled_names(css):
    """Class names that actually appear in selector position.

    Reading the whole file for `.name` counts a name that only ever appears
    in a comment - and this stylesheet comments above nearly every rule,
    often naming the class it is about. That would let a rule be deleted
    while its comment kept the test green: the exact silent pass this file
    exists to prevent.

    So comments go first, then the file is walked brace by brace. Whatever
    sits before an opening brace is a prelude; an at-rule prelude (@media,
    @supports) carries no class names, and a rule nested inside one is read
    like any other."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    out, buf = set(), []
    for ch in css:
        if ch == "{":
            prelude = "".join(buf).strip()
            if not prelude.startswith("@"):
                out.update(NAME.findall(prelude))
            buf = []
        elif ch == "}":
            buf = []
        else:
            buf.append(ch)
    return out


styled = styled_names(CSS)

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


print("\n-- one class, one block, within a file -------------------------------")
# A class written twice in the *same* stylesheet is a defect: the later block
# silently wins and the earlier one is dead. That is what happened when I added
# a .summary the file already had.
#
# The same class in app.css *and* in the design's components.css is a different
# thing - it is the migration, mid-way. The design is linked last and wins, and
# the app's copy is dead weight to be deleted as each screen moves across. So
# that is counted and listed rather than failed: it is the work remaining, and
# it should only ever go down.
from collections import Counter


def bare_blocks(css):
    blocks, buf, depth = [], [], 0
    for ch in re.sub(r"/\*.*?\*/", " ", css, flags=re.S):
        if ch == "{":
            if depth == 0:
                prelude = "".join(buf).strip()
                if not prelude.startswith("@"):
                    blocks.append(" ".join(prelude.split()))
            buf = []
            depth += 1
        elif ch == "}":
            depth -= 1
            buf = []
        else:
            buf.append(ch)
    return Counter(s for s in blocks if re.fullmatch(r"\.[a-z][\w-]*", s))


per_file = {}
for name in ("app.css", "tokens.css", "components.css", "app-shell.css"):
    f = SRC / "static" / "css" / name
    if f.exists():
        per_file[name] = bare_blocks(f.read_text(encoding="utf-8"))

twice = sorted(n for c in per_file.values() for n, k in c.items() if k > 1)
check("no class is defined twice inside one stylesheet",
      not twice, ", ".join(twice) if twice else "")
check("...and there are enough of them for that to mean something",
      sum(len(c) for c in per_file.values()) >= 100,
      str(sum(len(c) for c in per_file.values())))

app_own = set(per_file.get("app.css", {}))
design = set(per_file.get("components.css", {}))
overlap = sorted(app_own & design)
print(f"      still defined in both app.css and the design: {len(overlap)}")
if overlap:
    print("        " + " ".join(overlap))
    print("      (the design wins - these are the app rules to delete as each")
    print("       screen moves across, and the number should only go down)")


print("\n-- the reading is of selectors, not of comments --------------------")
# Guarding the guard. A comment that names a class must not be able to keep
# this file green after the rule itself is gone.
probe = ".seatbelt { color: red; }\n.x { color: blue; }"
check("a name in a selector is read", "seatbelt" in styled_names(probe))
check("a name in a comment is not",
      "seatbelt" not in styled_names("/* .seatbelt is gone now */\n" + probe.split("\n")[1]))
check("a rule inside @media is read like any other",
      "deep" in styled_names("@media (max-width: 900px) { .deep { color: red; } }"))


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
