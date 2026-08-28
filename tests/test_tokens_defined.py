"""
Every colour the stylesheet asks for has been given a value, in both themes.

`var(--accent)` on a token that was never defined does not throw and does not
warn. The declaration is simply dropped, and the element keeps whatever it
would have had otherwise - usually black text on a black panel, or a border
that quietly disappears. It is the same silent shape as a missing state class,
one layer further down.

This matters now because the 1.5 redesign renames the colour vocabulary. The
app calls things what they look like - `--ink`, `--panel`, `--cyan`, `--pink`.
The deck calls them what they are for - `--text`, `--surface`, `--accent`,
`--bad`. The second is the better vocabulary and it is the language every new
rule will be written in, so the rename happens before the rooms do. A rename
across 2,875 lines is exactly the kind of change that ends up nine-tenths
done, and this is what makes the last tenth visible.

The second check is the one that catches a half-finished theme: a token
defined for dark and forgotten for light leaves the light theme falling back
to the dark value, which usually still renders - just wrongly, and only for
the people using that theme.
"""

import re
import sys
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent
       / "src" / "static" / "css" / "app.css").read_text(encoding="utf-8")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def theme_block(css, theme):
    """The token declarations for one theme, by name."""
    m = re.search(r':root\[data-theme="' + theme + r'"\]\s*\{(.*?)\n\}', css, re.S)
    if not m:
        return {}
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


body = re.sub(r"/\*.*?\*/", " ", CSS, flags=re.S)

dark = theme_block(CSS, "dark")
light = theme_block(CSS, "light")

# A token asked for with a fallback - var(--d, 0ms) - is allowed to be
# undefined: the fallback is the answer. --d is set inline in the markup to
# stagger the reveal animation, and its fallback is what a rebuilt page would
# land on.
with_fallback = set(re.findall(r"var\(\s*(--[\w-]+)\s*,", body))
asked = set(re.findall(r"var\(\s*(--[\w-]+)", body))
defined_anywhere = set(re.findall(r"(--[\w-]+)\s*:", body))


print("\n-- every token the stylesheet uses has a value ---------------------")
print(f"      {len(asked)} asked for · {len(defined_anywhere)} defined · "
      f"{len(with_fallback)} carry a fallback")

orphans = sorted(asked - defined_anywhere - with_fallback)
check("no var() is read that nothing defines",
      not orphans, ", ".join(orphans) if orphans else "")


print("\n-- and both themes answer the same list ----------------------------")
print(f"      dark defines {len(dark)} · light defines {len(light)}")

check("there are two themes to compare", bool(dark) and bool(light))

# Light deliberately inherits some of dark's values rather than restating
# them - fonts, radii, easings and the rgb triples do not change with the
# theme. Only what light actually overrides has to be complete, so the
# comparison is the other way round: nothing may be light-only.
light_only = sorted(set(light) - set(dark))
check("no token exists in light that dark has never heard of",
      not light_only, ", ".join(light_only) if light_only else "")

# The colours light does not override, listed rather than asserted - some are
# meant to be shared. This is here so a genuinely missed one is visible.
shared = sorted(t for t in dark if t not in light)
print(f"      tokens dark defines and light inherits: {len(shared)}")
if shared:
    print("        " + " ".join(shared))


print("\n-- the typeface is on disk, not on the internet --------------------")
# A missing font file is silent in the worst way: @font-face fails, the next
# family in the stack is used, and the app looks almost right. Since the whole
# reason Poppins is bundled rather than linked is that Riplox must look correct
# with no network, "almost right" is exactly the failure to catch.
STATIC = Path(__file__).resolve().parent.parent / "src" / "static"
wanted = re.findall(r'url\("\.\./([^"]+\.woff2)"\)', CSS)
print(f"      {len(wanted)} font files asked for by the stylesheet")

absent = sorted({f for f in wanted if not (STATIC / f).is_file()})
check("every font the stylesheet asks for is in the app",
      not absent, ", ".join(absent) if absent else "")
check("...and it asks for some", len(wanted) >= 2, str(len(wanted)))

empty = sorted({f for f in wanted
                if (STATIC / f).is_file() and (STATIC / f).stat().st_size < 2000})
check("none of them is a stub", not empty, ", ".join(empty) if empty else "")

# Poppins is under the SIL Open Font License, which requires the licence to
# travel with the font. It is in the same folder, so it ships with it.
check("the font licence ships beside the font",
      (STATIC / "fonts" / "OFL.txt").is_file()
      and "SIL OPEN FONT LICENSE" in (STATIC / "fonts" / "OFL.txt").read_text(
          encoding="utf-8", errors="ignore"))

check("the stylesheet leads with the bundled face, and keeps a fallback",
      re.search(r'--font:\s*"Poppins",\s*"Segoe', CSS) is not None)


print("\n-- the theme is chosen by an attribute the page must carry ---------")
# :root[data-theme="..."] is the whole mechanism. It is also one of the eight
# data attributes test_ids_exist.py guards, and this is the other half of why
# that check exists.
check("the stylesheet still selects on data-theme",
      ':root[data-theme="dark"]' in CSS and ':root[data-theme="light"]' in CSS)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
