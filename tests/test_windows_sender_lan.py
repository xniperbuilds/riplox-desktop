"""The Windows sender keeping up with a PC that moved.

It has taken the local network since it shipped, but it learned the address
once - from the pairing link - and never again. So the first time the PC was
handed a different address, every send pinged somewhere nobody answered, waited
for the timeout, and fell back to the relay. Silently, on every send, until
somebody happened to pair again.

Worse, the old handling *wiped* the address when that happened. Losing it is
what made the failure permanent: there was then nothing to correct, so it never
came back on its own.

The PC now says where it is inside every sealed reply, so this is a matter of
reading what already arrived.
"""
import sys
import tempfile
from pathlib import Path

SEND = Path(__file__).resolve().parent.parent / "send-windows" / "src"
sys.path.insert(0, str(SEND))

import send                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:90]) if detail else ""))


print("\n-- reading what the PC offered -------------------------------------")
for offered, want, why in [
    (["192.168.1.5:47811"], "192.168.1.5:47811", "one address"),
    (["10.0.0.7:47811", "192.168.1.5:47811"], "10.0.0.7:47811",
     "several: the first, which is the PC's own default route"),
    ("192.168.1.5:47811", "192.168.1.5:47811", "a bare string, not a list"),
    (None, "", "nothing offered"),
    ([], "", "an empty list"),
    ({"a": 1}, "", "something that is not a list at all"),
    ([""], "", "an empty entry"),
    (["no-colon-here"], "", "no port"),
    ([":47811"], "", "no address"),
    (["x" * 200], "", "absurdly long"),
]:
    got = send._pick_lan(offered)
    check(f"{why} -> {want or '(nothing)'}", got == want, f"got {got!r}")

# Four is what the PC sends at most, and each one costs a timeout to try.
many = [f"192.168.{n}.5:47811" for n in range(1, 9)]
check("it never wanders past the first few", send._pick_lan(many) == "192.168.1.5:47811",
      send._pick_lan(many))


print("\n-- what a reply now carries ----------------------------------------")
# deliver() returns the PC's verdict. The address rides in the same sealed
# envelope, so a caller can keep up without a second request anywhere.
captured = {}
real_post, real_get, real_seal, real_unseal = (
    send._post, send._get, send.seal, send.unseal)
try:
    send.seal = lambda key, body: {"n": "x", "c": "y"}
    send._post = lambda url, body, timeout: {"ok": True}
    send._get = lambda url, timeout: {"ok": True, "ack": {"n": "x", "c": "y"}}
    send.unseal = lambda key, n, c: {"why": "queued",
                                     "lan": ["192.168.100.163:47811"]}
    # lan="" so the local attempt is skipped and the relay path is what runs.
    out = send.deliver("a" * 32, "k" * 43, "", {"kind": "link", "url": "https://x/y"})
    check("the verdict still comes through", out.get("why") == "queued", out)
    check("⭐ and the PC's current address comes with it",
          out.get("lan") == "192.168.100.163:47811", out)

    send.unseal = lambda key, n, c: {"why": "queued"}
    out = send.deliver("a" * 32, "k" * 43, "", {"kind": "link", "url": "https://x/y"})
    check("a PC with nothing to offer gives an empty one, not a crash",
          out.get("lan") == "", out)
finally:
    send._post, send._get, send.seal, send.unseal = (
        real_post, real_get, real_seal, real_unseal)


print("\n-- and the app stores it -------------------------------------------")
sys.path.insert(0, str(SEND))
import store                                                # noqa: E402

SANDBOX = Path(tempfile.mkdtemp(prefix="riploxsend-test-"))
store.folder = lambda: SANDBOX                              # never the real one
state = {}
store.load = lambda: dict(state)
store.save = lambda data: state.update(data)

import app                                                  # noqa: E402

state.clear()
state.update({"room": "a" * 32, "key": "k" * 43, "lan": "192.168.100.42:47811"})
app._learn_lan({"via": "relay", "why": "queued", "lan": "192.168.100.163:47811"},
               "192.168.100.42:47811")
check("⭐ a new address replaces the old one",
      state.get("lan") == "192.168.100.163:47811", state.get("lan"))

app._learn_lan({"via": "lan", "why": "queued", "lan": "192.168.100.163:47811"},
               "192.168.100.163:47811")
check("the same address writes nothing", state.get("lan") == "192.168.100.163:47811")

# ⚠ The old code wiped the address when a send fell back. That is what made the
# failure permanent - once gone, there was nothing left to correct.
app._learn_lan({"via": "relay", "why": "queued"}, "192.168.100.163:47811")
check("⭐ a reply with no address does NOT wipe what we had",
      state.get("lan") == "192.168.100.163:47811", state.get("lan"))

app._learn_lan({"via": "relay", "why": "", "error": "no network"},
               "192.168.100.163:47811")
check("...and neither does a failed send", state.get("lan") == "192.168.100.163:47811",
      state.get("lan"))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
