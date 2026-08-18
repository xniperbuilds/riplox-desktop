"""The WebSocket route, against a real relay running locally.

Start it first:
    cd relay && npx wrangler dev --port 8799 --local

What has to be true:
  * a socket opens and is handed anything already waiting
  * a link sent while the socket is open arrives on it, not on a poll
  * confirming by name clears it, and NOT confirming keeps it
  * the old /wait route still behaves exactly as it did
  * an unconfirmed message survives a reconnect
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import sharing

BASE = "http://127.0.0.1:8799"

# A fresh room per run. Sharing one across runs meant a message left
# unconfirmed by an earlier run came back in the next one and failed a check
# that was actually fine - a false failure that costs more time than the
# isolation does. Rooms are cheap: a Durable Object that is never touched
# again is evicted and costs nothing.
ROOM = f"{int(time.time() * 1000):012x}{os.getpid():04x}"[:16]

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + str(detail)[:90] if detail else ""))


def post(path, body):
    request = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Riplox-test"})
    with urllib.request.urlopen(request, timeout=20) as r:
        return json.loads(r.read().decode())


def get(path, timeout=40):
    request = urllib.request.Request(f"{BASE}{path}",
                                     headers={"User-Agent": "Riplox-test"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.loads(r.read().decode())


def envelope(tag):
    # Shape only - the relay cannot read these and only checks the pattern.
    return {"n": ("n" + tag).ljust(16, "x")[:16].replace("_", "x"),
            "c": ("payload" + tag).ljust(24, "y")}


sharing.relay_base = lambda: BASE

print("-- the relay is up ------------------------------------------------")
try:
    get("/pending/" + ROOM, timeout=10)
    print("  reachable")
except Exception as exc:
    print(f"  relay not reachable at {BASE}: {exc}")
    print("  start it with: cd relay && npx wrangler dev --port 8799 --local")
    sys.exit(2)

print("\n-- a socket opens, and a link sent afterwards arrives on it --------")
wire = sharing._Wire(f"ws://127.0.0.1:8799/ws/{ROOM}", timeout=25)
wire.send(json.dumps({"hello": 1}))
time.sleep(0.6)

env1 = envelope("01")
post(f"/send/{ROOM}", env1)

got = None
try:
    got = json.loads(wire.recv())
except Exception as exc:
    check("a message arrived on the socket", False, exc)

if got:
    names = [m["n"] for m in got.get("msgs", [])]
    check("a message arrived on the socket", env1["n"] in names, names)
    check("it carries the sealed payload untouched",
          any(m["c"] == env1["c"] for m in got.get("msgs", [])))

print("\n-- not confirming keeps it; confirming by name clears it -----------")
pending = get(f"/pending/{ROOM}")
check("it is counted as held while unconfirmed",
      (pending.get("held") or 0) >= 1, pending)

wire.send(json.dumps({"done": [env1["n"]]}))
time.sleep(0.8)
pending = get(f"/pending/{ROOM}")
check("⭐ confirming by name clears it", (pending.get("held") or 0) == 0, pending)

print("\n-- an unconfirmed message survives losing the socket ---------------")
env2 = envelope("02")
post(f"/send/{ROOM}", env2)
try:
    wire.recv()                      # it arrives...
except Exception:
    pass
wire.close()                         # ...and is never confirmed
time.sleep(0.5)

wire2 = sharing._Wire(f"ws://127.0.0.1:8799/ws/{ROOM}", timeout=25)
wire2.send(json.dumps({"hello": 1}))
again = None
try:
    again = json.loads(wire2.recv())
except Exception as exc:
    check("⭐ it comes back on the next socket", False, exc)
if again:
    names = [m["n"] for m in again.get("msgs", [])]
    check("⭐ it comes back on the next socket", env2["n"] in names, names)
wire2.send(json.dumps({"done": [env2["n"]]}))
time.sleep(0.5)
wire2.close()

print("\n-- the old /wait route is untouched --------------------------------")
env3 = envelope("03")
post(f"/send/{ROOM}", env3)
old = get(f"/wait/{ROOM}?hold=3&ack=1", timeout=20)
names = [m["n"] for m in old.get("msgs", [])]
check("a poll still receives", env3["n"] in names, names)
check("...and it is held until confirmed", (old.get("held") or 0) >= 1)
post(f"/done/{ROOM}", {"n": [env3["n"]]})
time.sleep(0.4)
check("...and /done still clears it", (get(f'/pending/{ROOM}').get("held") or 0) == 0)

print("\n-- an empty poll still returns after its hold ----------------------")
started = time.monotonic()
empty = get(f"/wait/{ROOM}?hold=2&ack=1", timeout=20)
took = time.monotonic() - started
check("it waited rather than answering at once", took >= 1.5, f"{took:.1f}s")
check("and returned nothing", not empty.get("msgs"))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
