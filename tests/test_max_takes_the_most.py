"""Max takes the most there is, and says how much before it starts.

Six real videos - 4K SDR, 4K HDR 60fps, 8K HDR, 8K SDR - captured with their
whole format table and, beside it, the format yt-dlp ITSELF selected when
given engine.format_args("max"). See tests/fixtures/max-picks.json.

⚠️ The recorded pick is the point. An earlier attempt at reasoning about this
sort measured format metadata, never ran the selector, and shipped a change
that made max return 1080p and 4K return 720p. So nothing here re-implements
the sort or predicts what it would choose: it reads what it chose, and asks
whether that was the most on offer and whether the number on the chip matched.

No network. The capture is the network part, and it already happened.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import engine                                              # noqa: E402

DATA = json.loads((HERE / "fixtures" / "max-picks.json").read_text(encoding="utf-8"))
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)[:96]) if detail else ""))


def size_of(f):
    return f.get("filesize") or f.get("filesize_approx") or 0


def as_bytes(text):
    """Turn "89.9 MB" back into a number, so it can meet a real one."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)", text or "")
    if not m:
        return 0
    scale = {"B": 1, "KB": 1024, "MB": 1024 ** 2,
             "GB": 1024 ** 3, "TB": 1024 ** 4}[m.group(2)]
    return float(m.group(1)) * scale


for row in DATA["videos"]:
    info = {"formats": row["formats"]}
    picked = row["picked"]
    vids = [f for f in row["formats"] if isinstance(f.get("height"), int)
            and f.get("vcodec") not in (None, "none")]
    auds = [f for f in row["formats"]
            if f.get("acodec") not in (None, "none") and not f.get("height")]
    top = max(f["height"] for f in vids)
    at_top = [f for f in vids if f["height"] == top]

    print("\n-- %s  (%s) %s" % (row["title"][:44], row["why"][:34], "-" * 8))

    # 1. It went to the tallest thing there is - 8K included, which is above
    #    every named rung the chips offer.
    check("max reaches the tallest height on offer",
          picked["height"] == top, "picked %sp, tallest %sp" % (picked["height"], top))

    # 2. Of the streams AT that height, it took the heaviest. On one of these
    #    videos that is VP9 at 1685 MB over AV1 at 1596 and SDR at 700.
    heaviest = max(at_top, key=size_of)
    chosen_id = picked["format_id"].split("+")[0]
    chosen = next((f for f in at_top if f.get("format_id") == chosen_id), None)
    check("and the heaviest stream at that height",
          chosen is not None and size_of(chosen) == size_of(heaviest),
          "took %s (%.0f MB), heaviest %s (%.0f MB)"
          % (chosen_id, size_of(chosen or {}) / 1048576,
             heaviest.get("format_id"), size_of(heaviest) / 1048576))

    # 3. The fattest audio, which is not always opus: on the HDR clips the
    #    biggest track is 5.1 AAC at 388 kbps against opus at 140.
    if auds and "+" in picked["format_id"]:
        fattest = max(auds, key=lambda f: f.get("abr") or 0)
        got = picked["format_id"].split("+")[1]
        check("and the fattest audio track",
              got == fattest.get("format_id"),
              "took %s, fattest %s (%s kbps, %s)"
              % (got, fattest.get("format_id"), fattest.get("abr"),
                 fattest.get("acodec")))

    # 4. ⚠️ The number on the chip against the file that really arrives. This
    #    is the check the m3u8 rendition slipped past: its declared 22,055
    #    kbps is a PEAK, and the size it implied was more than twice the file.
    #    ⚠️ Compared as the chip WRITES it, not as a percentage. A percentage
    #    fails on nothing but the display: 1,693 MB is 1.653 GB, which at one
    #    decimal place is "1.7 GB" and 2.8% away from itself. The property
    #    that matters is that the chip shows what this many bytes is called.
    rungs = engine._available_qualities(info, {})
    shown = rungs["sizes"].get("max", "")
    want = picked["bytes"]
    check("the size on the Max chip matches the file that arrives",
          want > 0 and shown == engine.human_bytes(want),
          "chip says %r, %d bytes reads as %r"
          % (shown or "nothing", want, engine.human_bytes(want) if want else "-"))

    # 5. Best and Max are the same file here, so they must carry one number.
    check("Best available carries the same number",
          rungs["sizes"].get("best") == rungs["sizes"].get("max"),
          "%r vs %r" % (rungs["sizes"].get("best"), rungs["sizes"].get("max")))

    # 6. ⚠️ NOT "the sizes fall as the rungs do". That was the first version of
    #    this check and it was wrong about the world, not about the code:
    #    YouTube's 480p is genuinely smaller than its 360p on two of these
    #    videos, because the rungs are not all in the same codec. The real
    #    invariant is that nothing capped can exceed the uncapped answer.
    ceiling = as_bytes(rungs["sizes"].get("max", ""))
    over = {k: v for k, v in rungs["sizes"].items()
            if k.isdigit() and as_bytes(v) > ceiling * 1.001}
    check("no named rung claims more than Max", not over,
          "%s against a Max of %s" % (over or "none", rungs["sizes"].get("max")))

    # ⚠️ And "no more than" is not enough on its own. A mutation that made
    # every rung report the tallest stream's size went straight past the check
    # above - all equal is not "more than" - and a 720p chip would have shown
    # the 4K number. So the smallest rung offered has to be genuinely smaller.
    below = [as_bytes(v) for k, v in rungs["sizes"].items()
             if k.isdigit() and int(k) < top and v]
    check("a rung below the top is smaller than Max",
          not below or min(below) < ceiling * 0.999,
          "smallest rung %.1f MB against Max %.1f MB"
          % ((min(below) if below else 0) / 1048576, ceiling / 1048576))

print("\n" + "=" * 70)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 70 + "\n")
raise SystemExit(1 if FAIL else 0)
