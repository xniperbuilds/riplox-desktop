"""
Stuck-check, and the confirmation that makes it meaningful.

The rule under test: a message is only cleared from the relay once this PC
says it dealt with THAT message by name. One that failed must still be there
afterwards - otherwise the check reports "nothing waiting" about a link that
was quietly dropped, which is worse than not checking.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-stuck-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import sharing                                              # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


class FakeRelay:
    """Stands in for the Worker, holding queue and flight the same way."""

    def __init__(self):
        self.queue = [{"n": "aaaaaaaa1", "c": "x" * 20, "at": 1_760_000_000_000},
                      {"n": "bbbbbbbb2", "c": "y" * 20, "at": 1_760_000_001_000}]
        self.flight = []
        self.calls = []

    def wait(self, ack):
        self.calls.append(("wait", ack))
        if self.flight and not ack:
            self.flight = []              # the old behaviour
        if self.queue:
            self.flight, self.queue = self.queue, []
        return {"ok": True, "msgs": [{"n": m["n"], "c": m["c"]} for m in self.flight],
                "held": len(self.flight)}

    def done(self, names):
        self.calls.append(("done", tuple(names)))
        before = len(self.flight)
        self.flight = [m for m in self.flight if m["n"] not in set(names)]
        return {"ok": True, "cleared": before - len(self.flight),
                "held": len(self.flight)}

    def pending(self):
        return {"ok": True, "waiting": len(self.queue), "held": len(self.flight),
                "oldest": min([m["at"] for m in self.queue + self.flight] or [0])}


print("\n-- the old behaviour, for the record -----------------------------")
relay = FakeRelay()
relay.wait(ack=False)
check("a batch goes into flight", len(relay.flight) == 2)
relay.wait(ack=False)
check("⚠ the next poll cleared it without any confirmation",
      len(relay.flight) == 0,
      "this is the loss the fix addresses")

print("\n-- with ack=1, only a real confirmation clears it -----------------")
relay = FakeRelay()
relay.wait(ack=True)
check("the batch is in flight", len(relay.flight) == 2)
relay.wait(ack=True)
check("⭐ polling again does NOT clear it", len(relay.flight) == 2,
      str(len(relay.flight)))

relay.done(["aaaaaaaa1"])
check("confirming one clears exactly that one", len(relay.flight) == 1)
check("...and the other is still held", relay.flight[0]["n"] == "bbbbbbbb2")

print("\n-- a half-handled batch keeps what failed ------------------------")
relay = FakeRelay()
got = relay.wait(ack=True)
handled, failed = [], []
for msg in got["msgs"]:
    (failed if msg["n"] == "bbbbbbbb2" else handled).append(msg["n"])
relay.done(handled)
check("the one that worked is gone", "aaaaaaaa1" not in
      [m["n"] for m in relay.flight])
check("⭐ the one that failed is still there", "bbbbbbbb2" in
      [m["n"] for m in relay.flight])
check("...so the check reports it as stuck", relay.pending()["held"] == 1)

print("\n-- what the PC asks and what it is told --------------------------")
real_urlopen = sharing.urllib.request.urlopen
relay = FakeRelay()
relay.wait(ack=True)
relay.done(["aaaaaaaa1"])


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


sharing.urllib.request.urlopen = lambda req, timeout=0: FakeResponse(relay.pending())
try:
    out = sharing.pending()
finally:
    sharing.urllib.request.urlopen = real_urlopen

check("it answers", out.get("ok") is True, str(out))
check("held is reported", out.get("held") == 1, str(out.get("held")))
check("stuck is the sum of both", out.get("stuck") == out.get("waiting") + out.get("held"))
check("it says how old, in words", bool(out.get("since")), out.get("since", ""))

print("\n-- it refuses politely rather than guessing ----------------------")
saved = engine.load_settings()
saved["share_lan_only"] = True
engine.save_settings(saved)
off = sharing.pending()
check("home-network-only is answered, not faked",
      off.get("ok") is False and "network" in off.get("error", "").lower(),
      str(off))
saved["share_lan_only"] = False
engine.save_settings(saved)

sharing.urllib.request.urlopen = lambda req, timeout=0: (_ for _ in ()).throw(
    OSError("no route to host"))
try:
    dead = sharing.pending()
finally:
    sharing.urllib.request.urlopen = real_urlopen
check("an unreachable relay is reported, not counted as zero",
      dead.get("ok") is False and dead.get("error"), str(dead)[:70])

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
