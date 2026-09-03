# -*- coding: utf-8 -*-
"""The change surface, exercised against the real functions.

Nothing here reads the code and concludes; every line runs something and prints
what came back. Riplox's own data is never touched - data_dir is redirected at
a temp folder before anything that writes.
"""
import io, json, os, sys, tempfile
from pathlib import Path

# ⚠️ Relative to this file, not a path off one machine. This was written as an
# absolute path on the machine it was first run on, which meant it could only
# ever run there - and it put that machine's user name into a public repository.
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
import engine                                                    # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  | " + detail) if detail else ""))


print("\n-- 1. the reverted format change, at the function " + "-" * 18)
for quality, must_have, must_not in (
        ("max", "res,vbr,abr", "proto"),
        ("best", "res,vcodec:h264,acodec:aac", "proto"),
        ("1080", None, "proto")):
    args = engine.format_args(quality, {})
    sort = args[args.index("-S") + 1] if "-S" in args else ""
    line = " ".join(args)
    ok = (must_not not in line) and (must_have is None or sort == must_have)
    check("format_args(%r) sort" % quality, ok, sort)

print("\n-- 2. the size, at its boundaries " + "-" * 33)
# _settled_size(bytes, held) - the band is 25%.
MB = 1048576.0
cases = [
    ("first reading is shown", 300 * MB, 0.0, lambda v: v > 0),
    ("a 5% move is ignored", 315 * MB, 300 * MB, lambda v: v == 300 * MB),
    ("a 30% rise is followed", 390 * MB, 300 * MB, lambda v: v > 300 * MB),
    ("a 30% FALL is followed too", 200 * MB, 300 * MB, lambda v: v < 300 * MB),
    ("under 10 MB is not rounded", 0.4 * MB, 0.0, lambda v: v == 0.4 * MB),
    ("a 100-byte file is not rounded to zero", 100.0, 0.0, lambda v: v == 100.0),
]
for name, size, held, want in cases:
    got = engine._settled_size(size, held)
    check(name, want(got), "%.2f MB" % (got / MB))

print("\n-- 3. the host's count, on every shape of queue " + "-" * 20)
import native_host                                               # noqa: E402
sandbox = Path(tempfile.mkdtemp(prefix="riplox-audit-"))
native_host.data_dir = lambda: sandbox

def write_queue(payload):
    io.open(sandbox / "queue.json", "w", encoding="utf-8").write(json.dumps(payload))

shapes = [
    ("no file at all", None, 0),
    ("empty list", [], 0),
    ("old format, no status", [{"url": "a"}, {"url": "b"}, {"url": "c"}], 3),
    ("new format, one paused", [{"status": "downloading"}, {"status": "queued"},
                                {"status": "paused"}], 2),
    ("new format, all done", [{"status": "done"}, {"status": "error"}], 0),
    ("garbage inside", ["not a dict", 7, None], 0),
    ("a dict instead of a list", {"jobs": [{"status": "downloading"}]}, 1),
]
for name, payload, want in shapes:
    if payload is None:
        (sandbox / "queue.json").unlink(missing_ok=True)
    else:
        write_queue(payload)
    got = native_host.running()
    check(name, got == want, "got %d, wanted %d" % (got, want))

print("\n-- 4. the host and the app agree on a version " + "-" * 22)
app_txt = (SRC / "app.py").read_text(encoding="utf-8")
import re
m = re.search(r'^VERSION\s*=\s*"([^"]+)"', app_txt, re.M)
check("native_host.VERSION == app.VERSION", m and native_host.VERSION == m.group(1),
      "host=%s app=%s" % (native_host.VERSION, m.group(1) if m else "?"))

print("\n" + "=" * 70)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("    FAILED: " + f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
