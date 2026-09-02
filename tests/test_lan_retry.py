"""A busy LAN port is a delay, not the end of home-network sharing.

Measured on a real machine: a second copy of Riplox held port 47811, the copy
being used could not bind it, and LAN sharing was dead for that entire run.
_serve_lan bound once and returned on OSError, and start() could not bring it
back because _started was already True - so only switching sharing off and on
rebound it, which nothing on the screen suggested.

Everything here runs the real _serve_lan against a real socket. Nothing is
stubbed, because what is being checked is what the thread does when the bind
fails - and a stub would be checking the stub.

⚠ Its own port, never 47811. A Riplox running on this machine must not have
its listener disturbed by a test.
"""
import socket
import sys
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import sharing                                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:100]) if detail else ""))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(want, seconds=8.0):
    """Poll the status until it says want, or give up and return what it says."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if sharing._status["lan"] == want:
            return want
        time.sleep(0.05)
    return sharing._status["lan"]


PORT = free_port()
sharing.LAN_PORT = PORT
# Long enough to be a real wait, short enough that this test is not one.
sharing.LAN_RETRY = 0.4

print("\n-- two copies cannot share one port " + "-" * 33)

# ⚠ THE bug, and the reason this file exists. Measured on Windows 11 before
# the fix: HTTPServer sets allow_reuse_address = 1, and there SO_REUSEADDR
# lets a second process bind a port that is already live and take its
# connections. Two copies of Riplox both said "listening" on 47811, both
# screens said everything was fine, and the phone reached whichever one
# Windows picked. Nothing failed, so nothing could be reported.
guard_port = free_port()
held = sharing._LanServer(("0.0.0.0", guard_port), sharing._LanHandler)
try:
    second = sharing._LanServer(("0.0.0.0", guard_port), sharing._LanHandler)
    second.server_close()
    stolen = True
except OSError:
    stolen = False
check("a second listener is refused the port rather than stealing it",
      not stolen, "port %d" % guard_port)
check("the listener does not ask for address reuse",
      sharing._LanServer.allow_reuse_address is False,
      repr(sharing._LanServer.allow_reuse_address))

# ⚠ The direction that actually matters, and the one the check above does not
# cover: OURS is up first and something else tries to take it. Not asking for
# reuse only stops us stealing - it does not stop an OLDER Riplox, which still
# sets SO_REUSEADDR, from stealing from us. On Windows that steal succeeds
# unless the holder set SO_EXCLUSIVEADDRUSE. Old builds will be on this
# machine for a long time, so this is not hypothetical.
thief = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    thief.bind(("0.0.0.0", guard_port))
    robbed = True
except OSError:
    robbed = False
thief.close()
check("an older copy asking for reuse cannot take a live port from us",
      not robbed, "port %d" % guard_port)
held.server_close()

print("\n-- the port is taken when sharing starts " + "-" * 28)

# Held before the listener ever runs - the exact shape of the real fault, a
# second copy of Riplox already sitting on the fixed port. SO_REUSEADDR here
# on purpose: that is what an OLDER Riplox build sets, and the new one must
# refuse to take the port from it rather than quietly sharing it.
squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
squatter.bind(("0.0.0.0", PORT))
squatter.listen(1)

sharing._stop.clear()
thread = threading.Thread(target=sharing._serve_lan, daemon=True)
thread.start()

check("the listener reports itself unavailable", wait_for("unavailable") == "unavailable",
      sharing._status["lan"])
check("it says the port is the reason, and that it will keep trying",
      str(PORT) in sharing._status["lan_error"]
      and "again" in sharing._status["lan_error"].lower(),
      sharing._status["lan_error"])
# ⚠ Its own key. The relay clears _status["error"] on every successful
# connect, which would wipe this explanation moments after it was written.
check("the reason has a key the relay cannot clear",
      sharing._status["lan_error"] and "lan_error" in sharing._status,
      "error=%r lan_error set=%s"
      % (sharing._status["error"], bool(sharing._status["lan_error"])))
check("the thread is still alive rather than having given up", thread.is_alive())

print("\n-- the other copy goes away " + "-" * 41)
squatter.close()
check("the listener takes the port on its own, with nobody toggling anything",
      wait_for("listening") == "listening", sharing._status["lan"])
check("the fault line clears itself", sharing._status["lan_error"] == "",
      repr(sharing._status["lan_error"]))

print("\n-- a real phone request is answered afterwards " + "-" * 22)
import json                                                 # noqa: E402
import urllib.error                                         # noqa: E402
import urllib.request                                       # noqa: E402
try:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/lan-ping", data=json.dumps({}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=6) as answer:
        code = answer.status
except urllib.error.HTTPError as exc:
    code = exc.code
except Exception as exc:                                    # noqa: BLE001
    code = repr(exc)
check("the recovered listener really serves, not just reports",
      isinstance(code, int), code)

print("\n-- switching sharing off stops it " + "-" * 35)
# ⚠ Not a sleep-and-hope: the wait is on the event, so this has to be quick.
began = time.time()
sharing.stop()
thread.join(timeout=5)
check("the thread ends", not thread.is_alive(), "%.2fs" % (time.time() - began))
check("it ends promptly rather than after a full retry wait",
      time.time() - began < 3.0, "%.2fs" % (time.time() - began))
check("a switched-off sharing reports no fault",
      sharing._status["lan_error"] == "", repr(sharing._status["lan_error"]))

print("\n-- the screen is given something to say it with " + "-" * 21)
# The browser check for this lives in the scratchpad, because it can only see
# the fault when something else is really holding 47811. What is durable, and
# what actually broke, is the wiring: state() has to carry the reason, and the
# screen has to have somewhere to put it. Before this fix state() sent "lan"
# and "error" and the Sharing screen rendered neither.
sharing._status["lan"] = "unavailable"
sharing._status["lan_error"] = "the reason"
sharing._status["error"] = ""
try:
    said = sharing.state()
except Exception as exc:                                    # noqa: BLE001
    said = {"__failed__": repr(exc)}
check("state() carries the LAN reason to the screen",
      said.get("lan") == "unavailable" and said.get("lan_error") == "the reason",
      "lan=%r lan_error=%r" % (said.get("lan"), said.get("lan_error")))

markup = (Path(__file__).resolve().parent.parent
          / "src" / "templates" / "index.html").read_text(encoding="utf-8")
script = (Path(__file__).resolve().parent.parent
          / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")
check("the screen has a place for it", 'id="shareFault"' in markup)
check("and something that fills it in", 'shareFault' in script and 's.lan_error' in script)
sharing._status["lan_error"] = ""

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
