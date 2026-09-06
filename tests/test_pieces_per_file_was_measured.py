"""Why "Pieces per file" is four, written down where it can go red.

On 3 September it went 4 -> 16, together with the engine channel and the
YouTube helper, because one machine hitting repeated https failures was found
in all three states. Three changes, one observation: that establishes the
combination and not the cause.

The other two have since been answered on their own. This one was measured on
7 September, one value at a time - same video, same connection, same helper,
40 MB per run - and it buys nothing:

    pieces   speed          kept when an attempt is interrupted
    1        2.33 MB/s      84.3%
    2        2.33 MB/s      83.6%
    4        2.28-2.31      64.2%
    8        2.25-2.30       7.1% / 20.0%
    16       2.17-2.25       0.0% / 0.0%

Seven per cent of speed across the whole range, with the fastest cells at the
bottom of it - and at sixteen an interrupted attempt commits NOTHING, because
a 32-fragment stream has half of itself in flight. The next attempt fetches it
all again, which is the download that "kept going and never finished".

This file holds the numbers to the code. It cannot re-measure a connection,
so what it checks is the reasoning: the default is one the measurement
supports, the value that measured worst is not the default, and the offer
that used to push people towards it now points the other way.

    python tests/test_pieces_per_file_was_measured.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-pieces-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []

# What each value kept when an attempt was interrupted at 40 MB, measured
# 07-Sep-2026 on cWMxCE2HTag at max. Two runs where there are two figures.
MEASURED_KEPT = {1: 84.3, 2: 83.6, 4: 64.2, 8: 7.1, 16: 0.0}


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


print("-- the default is one the measurement supports " + "-" * 22)

pieces = engine.DEFAULT_SETTINGS["fragments"]
check("it is a value that was actually measured", pieces in MEASURED_KEPT,
      pieces)
check("it keeps more than half of an interrupted attempt",
      MEASURED_KEPT.get(pieces, 0) > 50,
      "%s%% at %s pieces" % (MEASURED_KEPT.get(pieces), pieces))
check("and it is not the value that measured worst",
      pieces != max(MEASURED_KEPT, key=lambda p: p),
      "16 kept 0%% of 40 MB, twice")

print("\n-- the offer no longer pushes anyone towards sixteen " + "-" * 16)

old, new = engine._WAS_DEFAULT["fragments"]
check("it moves people OFF the value that keeps nothing", old == 16, old)
check("and onto the default", new == pieces, new)
check("which is the direction the measurement points",
      MEASURED_KEPT[new] > MEASURED_KEPT[old],
      "%s%% -> %s%%" % (MEASURED_KEPT[old], MEASURED_KEPT[new]))

print("\n-- sixteen is still reachable by hand " + "-" * 31)


def sent(value):
    args = [str(a) for a in engine.extra_args(
        dict(engine.DEFAULT_SETTINGS, fragments=value), "best")]
    return args[args.index("--concurrent-fragments") + 1]


check("choosing sixteen sends sixteen", sent(16) == "16", sent(16))
check("choosing one sends one", sent(1) == "1", sent(1))
check("the default is what an untouched install sends",
      sent(pieces) == str(pieces), sent(pieces))

print("\n-- and every value the window offers is a real one " + "-" * 17)

# The Settings dropdown is written out in the template; these are its options.
shown = [1, 2, 4, 8, 16]
for n in shown:
    check("%-2d survives the clamp" % n, sent(n) == str(n), sent(n))
check("the default is one of the choices", pieces in shown, pieces)

print("\n-- the reason is in the code, not only in a report " + "-" * 17)

source = (SRC / "engine.py").read_text(encoding="utf-8", errors="replace")
where = source.index('"fragments":')
near = source[max(0, where - 1800):where]
check("the measurement is written beside the default",
      "2.33 MB/s" in near and "84.3%" in near,
      "a number nobody can find is a number nobody can check")
check("and so is the reason it is not one or two",
      "ONE connection" in near,
      "the honest limit of the measurement belongs with it")

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
