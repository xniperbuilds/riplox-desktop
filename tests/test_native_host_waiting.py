"""What the browser is told about links it handed over.

The extension could say "handed to Riplox" and nothing more. It could not tell
"Riplox took it and is downloading" from "Riplox is closed, this is sitting in
a file". Reading 60 issues on a comparable extension, that exact confusion is
the single loudest complaint in the category - louder than any missing feature.

The answer comes out of the inbox itself. Riplox drains that file every 1.5
seconds while it runs, so a link with an old timestamp means nothing is
draining it. No heartbeat file, no port, nothing that can claim to be fresh
while being stale - which is how instance.json once pointed at a dead port for
five days while Riplox ran on another.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import native_host                                          # noqa: E402

TMP = Path(tempfile.mkdtemp())
native_host.data_dir = lambda: TMP                          # never touch the real one

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:80]) if detail else ""))


def inbox(items):
    (TMP / "inbox.json").write_text(json.dumps(items), encoding="utf-8")


print("\n-- no inbox at all -------------------------------------------------")
try:
    (TMP / "inbox.json").unlink()
except OSError:
    pass
check("nothing waiting, and it does not throw",
      native_host.waiting() == {"waiting": 0, "oldest": 0.0}, native_host.waiting())

print("\n-- a fresh link, about to be picked up -----------------------------")
inbox([{"url": "https://x/1", "quality": "best", "at": time.time()}])
answer = native_host.waiting()
check("one waiting", answer["waiting"] == 1, answer)
check("and it is young", answer["oldest"] < 5, answer)

print("\n-- links that have sat there: Riplox is not running -----------------")
inbox([{"url": "https://x/1", "at": time.time() - 300},
       {"url": "https://x/2", "at": time.time()}])
answer = native_host.waiting()
check("counts both", answer["waiting"] == 2, answer)
# The newest link is seconds old. Reporting that would say "being collected"
# about an inbox nothing has touched in five minutes.
check("it reports the OLDEST, not the newest", answer["oldest"] > 250, answer)

print("\n-- rubbish in the file ---------------------------------------------")
(TMP / "inbox.json").write_text("{not json", encoding="utf-8")
check("bad json reads as nothing, not a crash",
      native_host.waiting() == {"waiting": 0, "oldest": 0.0})
inbox({"not": "a list"})
check("wrong shape reads as nothing, not a crash",
      native_host.waiting() == {"waiting": 0, "oldest": 0.0})
inbox([{"url": "https://x/1"}])
check("an entry with no timestamp does not go negative",
      native_host.waiting()["oldest"] >= 0, native_host.waiting())

print("\n-- a clock that stepped backwards ----------------------------------")
inbox([{"url": "https://x/1", "at": time.time() + 9999}])
check("never reports a negative age",
      native_host.waiting()["oldest"] >= 0, native_host.waiting())

print("\n-- the old answer is still in the new one --------------------------")
inbox([{"url": "https://x/1", "at": time.time()}])
reply = {"ok": True, "active": native_host.running(), **native_host.waiting()}
check("active survived", "active" in reply, reply)
check("waiting and oldest joined it", "waiting" in reply and "oldest" in reply, reply)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
