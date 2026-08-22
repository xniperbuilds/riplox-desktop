"""The long poll, against a real relay - does it still answer the instant a
link lands, now that the waiting happens in the Worker instead of in the room?

Start the relay first:
    cd relay && npx wrangler dev --port 8799 --local

This is the test that guards the promise the whole change was made under: the
poll must be no slower than it was. The hold moved out of the Durable Object so
that an idle room can hibernate and stop being billed for wall-clock time - but
if the price of that were a link sitting around until the next poll came due,
the change would be a downgrade dressed as a saving.

What has to be true:
  * a link sent DURING a hold comes back at once, not when the hold expires
  * an empty hold still runs its full length rather than returning early
  * the response is the same shape the PC has always been given
  * a second link during a second hold behaves identically - the socket the
    Worker holds is opened and closed per poll, so this is where a leak would
    show up
"""
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8799"
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


def get(path, timeout=60):
    request = urllib.request.Request(f"{BASE}{path}",
                                     headers={"User-Agent": "Riplox-test"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.loads(r.read().decode())


def envelope(tag):
    return {"n": f"n{tag}" + "x" * (12 - len(tag)), "c": "Zm9vYmFyYmF6cXV4c2VjcmV0"}


def poll_in_background(hold, out):
    """Start a /wait and record what came back and how long it took."""
    started = time.monotonic()
    try:
        out["body"] = get(f"/wait/{ROOM}?hold={hold}&ack=1", timeout=hold + 20)
    except Exception as exc:                       # noqa: BLE001
        out["error"] = str(exc)
    out["took"] = time.monotonic() - started


try:
    get("/now", timeout=8)
except Exception as exc:                           # noqa: BLE001
    print(f"\n  relay not reachable at {BASE} - start wrangler dev first\n  {exc}\n")
    raise SystemExit(2)

print("\n-- the room has to be watched before it will take a send ----------")
# /send refuses a room no PC has ever polled. One short poll is what makes it
# real - and it is also the first proof that a poll with nothing waiting
# returns on time rather than erroring.
started = time.monotonic()
first = get(f"/wait/{ROOM}?hold=2&ack=1")
check("an empty poll returns after its hold", 1.5 <= time.monotonic() - started < 8,
      f"{time.monotonic() - started:.1f}s")
check("...with the usual empty body", first.get("ok") is True and not first.get("msgs"), first)

print("\n-- a link sent DURING a hold comes back at once -------------------")
out = {}
worker = threading.Thread(target=poll_in_background, args=(20, out))
worker.start()
time.sleep(1.5)                                    # let the hold get going
env1 = envelope("01")
post(f"/send/{ROOM}", env1)
worker.join(timeout=30)

check("the poll returned", "body" in out, out.get("error"))
names = [m["n"] for m in (out.get("body") or {}).get("msgs", [])]
check("⭐ it carried the link", env1["n"] in names, names)
check("⭐ and did NOT wait out the 20s hold", (out.get("took") or 99) < 6,
      f"{out.get('took', 0):.1f}s")
check("...the sealed payload is untouched",
      (out.get("body") or {}).get("msgs", [{}])[0].get("c") == env1["c"])
check("...and it is reported as held until confirmed",
      ((out.get("body") or {}).get("held") or 0) >= 1, out.get("body"))

print("\n-- it stays until confirmed, then goes ---------------------------")
still = get(f"/pending/{ROOM}")
check("still held before /done", (still.get("held") or 0) == 1, still)
post(f"/done/{ROOM}", {"n": [env1["n"]]})
time.sleep(0.4)
cleared = get(f"/pending/{ROOM}")
check("cleared after /done", (cleared.get("held") or 0) == 0, cleared)

print("\n-- and again, because the Worker opens a socket per poll ----------")
# A second round through the same room. If the per-poll socket were leaking or
# the room were left unable to hibernate, this is where it would start
# behaving differently from the first time.
out2 = {}
worker2 = threading.Thread(target=poll_in_background, args=(20, out2))
worker2.start()
time.sleep(1.5)
env2 = envelope("02")
post(f"/send/{ROOM}", env2)
worker2.join(timeout=30)

names2 = [m["n"] for m in (out2.get("body") or {}).get("msgs", [])]
check("⭐ the second link arrived too", env2["n"] in names2, names2)
check("⭐ just as quickly", (out2.get("took") or 99) < 6, f"{out2.get('took', 0):.1f}s")

print("\n-- a link left waiting is handed to the NEXT poll -----------------")
# Nothing is listening at the moment it is sent, so it has to sit in the queue
# and be picked up by the poll that follows - the "your PC was off" case.
post(f"/done/{ROOM}", {"n": [env2["n"]]})
env3 = envelope("03")
post(f"/send/{ROOM}", env3)
time.sleep(0.4)
started = time.monotonic()
late = get(f"/wait/{ROOM}?hold=20&ack=1")
took = time.monotonic() - started
names3 = [m["n"] for m in late.get("msgs", [])]
check("⭐ it was waiting and came straight back", env3["n"] in names3, names3)
check("...without holding at all", took < 4, f"{took:.1f}s")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
