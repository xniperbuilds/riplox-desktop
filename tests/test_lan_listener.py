"""The two things a paired phone asks this PC directly, against the real listener.

When the phone and the PC are on the same Wi-Fi there is no reason to send a
link out to a data centre and back. The phone asks two questions of this
machine: "are you the PC I am paired with?" and "here is a sealed envelope".

Both are answered by a real HTTP server started here - not a stub - because
what is being checked is the thing the phone will actually talk to.

⚠ The whole exchange has to be safe against a stranger. Anyone on a cafe Wi-Fi
can reach this port. So a message that cannot be opened must teach them
nothing, and the ping must not hand out anything that would let them send one.
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import sharing                                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:90]) if detail else ""))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# A port of its own, so this never argues with a Riplox that is already running
# on this machine.
PORT = free_port()
sharing.LAN_PORT = PORT

server = threading.Thread(target=sharing._serve_lan, daemon=True)
server.start()

BASE = f"http://127.0.0.1:{PORT}"
for _ in range(60):
    try:
        urllib.request.urlopen(f"{BASE}/lan-ping", timeout=1).close()
        break
    except Exception:                                       # noqa: BLE001
        time.sleep(0.1)


def call(path, body=None, timeout=6):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, json.loads(answer.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:                                   # noqa: BLE001
            return exc.code, {}


print("\n-- is that you? ----------------------------------------------------")
status, said = call("/lan-ping")
check("the listener is up and answers", status == 200, f"{status} {said}")
check("it says yes", said.get("ok") is True, said)
check("⭐ and names the room, so the phone can tell it is the right PC",
      said.get("room") == sharing.load()["room"], said)
# The room is not a secret - the phone already has it. The key is, and the key
# is what a message has to be sealed with, so this hands over nothing useful
# to somebody who does not already have it.
check("...and nothing else at all", set(said) == {"ok", "room"}, sorted(said))


print("\n-- a stranger on the same Wi-Fi ------------------------------------")
status, said = call("/lan-send", {"n": "AAAAAAAAAAAA", "c": "AAAAAAAAAAAAAAAA"})
check("a message it cannot open is refused", status == 200 and said.get("ok") is False,
      f"{status} {said}")
check("⭐ and the refusal teaches them nothing", "why" not in said, said)


print("\n-- rubbish ---------------------------------------------------------")
for name, body in [("empty", {}), ("wrong shape", {"n": 1, "c": []}),
                   ("missing cipher", {"n": "AAAAAAAAAAAA"})]:
    status, said = call("/lan-send", body)
    check(f"{name} is refused without falling over",
          status == 200 and said.get("ok") is False, f"{status} {said}")

request = urllib.request.Request(f"{BASE}/lan-send", data=b"{not json",
                                 headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=6) as answer:
        status, said = answer.status, json.loads(answer.read().decode())
except urllib.error.HTTPError as exc:
    status, said = exc.code, {}
check("broken json is refused without falling over", said.get("ok") is False,
      f"{status} {said}")


print("\n-- a body far too big to be an envelope ----------------------------")
status, said = call("/lan-send", {"n": "A" * 12, "c": "Z" * (sharing.MAX_BODY + 500)})
check("⭐ oversized is refused by length, before anything is parsed",
      status == 413, f"{status} {said}")


print("\n-- anything else on this port --------------------------------------")
# The listener answers exactly two things. It cannot open a file, read a
# setting, or reach the app's own API - and that is the promise in the module
# docstring, so it is worth a test rather than a comment.
for path in ["/", "/api/settings", "/lan-send", "/../share.json", "/queue.json"]:
    status, said = call(path)
    check(f"GET {path} is not served", status == 404, f"{status} {said}")


print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
