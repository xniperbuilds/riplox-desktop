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
# Every stylesheet the page loads, not just the first one. The interface is a
# single app.css today; a second file linked after it would still be defining
# classes, and reading only one of them reported perfectly styled names as
# unstyled the last time there were two.
STYLESHEETS = ("app.css", "tokens.css", "components.css", "app-shell.css")
CSS = "\n".join(
    (SRC / "static" / "css" / name).read_text(encoding="utf-8")
    for name in STYLESHEETS
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
SCRIPT_ONLY = {
    # A marker the script finds itself by - querySelector(".drop-note") - on a
    # line that already carries `note`, which is the class doing the styling.
    "drop-note",
}


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
#
# Three ways in. Markup the script writes as a string; elements it builds and
# assigns a className to - about forty names arrive that second way, and until
# this line they went on the page without this file ever seeing them; and the
# element factory, `mk("span", "set-cat-count")`, which assigns its second
# argument to className out of sight of every pattern above. Thirteen call
# sites, and this file was blind to all of them - the same silent failure the
# whole test exists for, one line of syntax away.
CLASS_SOURCES = [
    r'class=\\?"([^"\'+]*)',
    r'\.className\s*=\s*"([^"\'+]*)',
    r'\.classList\.(?:add|remove|toggle)\(\s*"([\w-]+)"',
    r'\bmk\(\s*"[a-z]+"\s*,\s*"([^"\'+]*)"',
]
for pattern in CLASS_SOURCES:
    for chunk in re.findall(pattern, JS):
        for n in chunk.split():
            # A trailing hyphen means the literal was cut off by a
            # concatenation - `"health-row is-" + row.state` - so the stem is
            # not a class anyone wrote.
            if re.fullmatch(r"[a-z][\w-]*", n) and not n.endswith("-"):
                authored.add(n)
print(f"      {len(authored)} distinct classes written from JavaScript")

# Rename the factory and the pattern above quietly stops matching: no error,
# no fewer classes reported, just a file that has gone blind again. So it has
# to still be there, under that name, with the class as its second argument.
check("the element factory this file reads is still mk(tag, cls)",
      re.search(r"function mk\(\s*tag\s*,\s*cls\s*\)", JS) is not None)

homeless = sorted(c for c in authored if c not in styled and c not in SCRIPT_ONLY)
check("every class the script authors is defined in the stylesheet",
      not homeless,
      "no rule for: " + ", ".join(homeless[:10]) if homeless else "")
check("...and the script really does author markup", len(authored) >= 20,
      str(len(authored)))


print("\n-- and every class it looks the page up by is worn by something ------")
# The third silence. The two checks above cover classes the script writes;
# this covers the ones it reads. querySelectorAll(".tab-label") on a page with
# no .tab-label returns an empty list, the loop runs zero times, and whatever
# depended on it is gone with nothing thrown - which is how the palette lost
# its Go-to group when the rail was renamed, and kept it lost for three
# commits.
#
# Checked against the markup, not the stylesheet: a class can be styled and
# worn by nothing at all, which is the state this is looking for.
HTML_FILE = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
in_html = set()
for chunk in re.findall(r'class="([^"{]*)"', HTML_FILE):
    in_html.update(chunk.split())

worn = in_html | authored

looked_up = set()
for sel in re.findall(r'querySelector(?:All)?\(\s*"([^"]+)"', JS):
    # One selector can name several classes - ".drow.flat", ".chip.is-on".
    looked_up.update(re.findall(r"\.([a-zA-Z][\w-]*)", sel))

print(f"      {len(looked_up)} classes read by a selector \u00b7 {len(worn)} worn somewhere")
stale = sorted(c for c in looked_up if c not in worn and c not in SCRIPT_ONLY)
check("no selector reads a class that nothing on the page wears",
      not stale, ", ".join(stale) if stale else "")
check("...and there are enough selectors for that to mean something",
      len(looked_up) >= 10, str(len(looked_up)))


print("\n-- one class, one block, within a file -------------------------------")
# A class written twice in the *same* stylesheet is a defect: the later block
# silently wins and the earlier one is dead. That is what happened when I added
# a .summary the file already had.
#
# The same class in two *different* stylesheets is worse and harder to see: the
# later file wins for whichever properties it names and the earlier one keeps
# the rest, so one component ends up wearing half of each. `.toggle.on i` got a
# translate from one file and a left from the other, both applied, and that was
# a switch that would not switch. Listed, because two files is a state the app
# can be in legitimately, and it should only ever go down.
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
for name in STYLESHEETS:
    f = SRC / "static" / "css" / name
    if f.exists():
        per_file[name] = bare_blocks(f.read_text(encoding="utf-8"))

twice = sorted(n for c in per_file.values() for n, k in c.items() if k > 1)
check("no class is defined twice inside one stylesheet",
      not twice, ", ".join(twice) if twice else "")
check("...and there are enough of them for that to mean something",
      sum(len(c) for c in per_file.values()) >= 100,
      str(sum(len(c) for c in per_file.values())))

overlap = sorted(
    n for i, a in enumerate(per_file.values())
    for b in list(per_file.values())[i + 1:]
    for n in set(a) & set(b)
)
print(f"      classes defined in more than one stylesheet: {len(overlap)}")
if overlap:
    print("        " + " ".join(overlap))
    print("      (the file linked last wins, property by property - these are")
    print("       the rules to merge, and the number should only go down)")


print("\n-- the reading is of selectors, not of comments --------------------")
# Guarding the guard. A comment that names a class must not be able to keep
# this file green after the rule itself is gone.
probe = ".seatbelt { color: red; }\n.x { color: blue; }"
check("a name in a selector is read", "seatbelt" in styled_names(probe))
check("a name in a comment is not",
      "seatbelt" not in styled_names("/* .seatbelt is gone now */\n" + probe.split("\n")[1]))
check("a rule inside @media is read like any other",
      "deep" in styled_names("@media (max-width: 900px) { .deep { color: red; } }"))


print("\n-- what this catches when the stylesheet is rewritten --------------")
# Stated rather than assumed: replace app.css and every name above has to exist
# in the new sheet or be renamed in the script. This is the test that makes
# that a red line instead of a quiet one - which is the whole reason a swap of
# the interface can be undone in an afternoon.
check("the two lists together cover both silent failures",
      len(state) > 0 and len(authored) > 0,
      f"{len(state)} state + {len(authored)} authored")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
