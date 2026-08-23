"""Where this PC tells a paired phone it can be reached.

The point: when the phone and the PC are on the same Wi-Fi there is no reason
to send a link out to Cloudflare and back. The phone cannot know where the PC
is, though - it pairs from a typed code, and the PC's address moves with DHCP.
So the PC says where it is, inside the sealed verdict it already sends after
every message.

Two things this guards, both of which fail silently if they break:

  * The address must be one a phone could actually reach. Handing over a
    link-local or loopback address, or a VPN adapter's, means the phone spends
    a timeout on every single send and falls back to the relay each time, for
    as long as the pairing lasts, with nothing reported anywhere.

  * It must be read fresh. A laptop changes networks without restarting
    Riplox, and an address cached at startup is wrong the moment it does.
"""
import json
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import sharing                                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:90]) if detail else ""))


print("\n-- what counts as an address a phone could reach --------------------")
for address, want in [
    ("192.168.1.5", True), ("10.0.0.7", True), ("172.16.3.9", True),
    ("169.254.1.1", False),      # an adapter with no network talking to itself
    ("127.0.0.1", False),        # only this machine can reach this
    ("8.8.8.8", False),          # public: not a LAN address at all
    ("", False), ("not-an-ip", False), ("::1", False),
]:
    got = sharing._own_network(address)
    check(f"{address or '(empty)'} -> {want}", got is want, got)


print("\n-- the real machine ------------------------------------------------")
found = sharing.lan_addresses()
check("it finds at least one address here", len(found) >= 1, found)
check("every one of them is reachable-looking",
      all(sharing._own_network(a) for a in found), found)
check("no duplicates", len(found) == len(set(found)), found)
check("lan_ip() is the first of them",
      sharing.lan_ip() == (found[0] if found else ""), sharing.lan_ip())


print("\n-- read fresh, never cached ----------------------------------------")
# A laptop moves between networks while Riplox stays open. If this were cached
# the phone would be sent to yesterday's address for the rest of the session.
real = sharing._own_network
try:
    sharing._own_network = lambda a: False       # pretend every adapter vanished
    check("no usable address -> empty list", sharing.lan_addresses() == [],
          sharing.lan_addresses())
    check("...and lan_ip() is empty, not stale", sharing.lan_ip() == "",
          sharing.lan_ip())
finally:
    sharing._own_network = real
check("it comes back when the network does", sharing.lan_addresses() == found,
      sharing.lan_addresses())


print("\n-- the verdict the phone actually receives --------------------------")
sealed = {}
real_seal = sharing._seal


def capture(key_b64, body):
    sealed.clear()
    sealed.update(body)
    return {"n": "x", "c": "y"}


class _Swallow:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


real_open = sharing.urllib.request.urlopen
try:
    sharing._seal = capture
    sharing.urllib.request.urlopen = lambda *a, **k: _Swallow()

    sharing._reply("a" * 43, "rid1234567", "queued")
    check("the verdict still says what happened", sealed.get("why") == "queued", sealed)
    check("⭐ and carries where to find this PC", isinstance(sealed.get("lan"), list)
          and len(sealed["lan"]) >= 1, sealed.get("lan"))
    check("...as address:port", all(":" in a for a in sealed.get("lan", [])),
          sealed.get("lan"))
    check("...on the port the listener is actually on",
          all(a.endswith(f":{sharing.LAN_PORT}") for a in sealed.get("lan", [])),
          sealed.get("lan"))
    check("...and never more than a handful", len(sealed.get("lan", [])) <= 4,
          sealed.get("lan"))

    # This machine has one address, so the cap above cannot fail here on its
    # own. A PC with a VPN, WSL, Docker and two adapters is the case that
    # matters, and the phone has to try each one in turn before giving up -
    # so the list has to stay short whatever this machine happens to look like.
    real_addresses = sharing.lan_addresses
    try:
        sharing.lan_addresses = lambda: [f"192.168.{n}.5" for n in range(1, 8)]
        sharing._reply("a" * 43, "rid1234567", "queued")
        check("⭐ a machine with seven adapters still sends at most four",
              len(sealed.get("lan", [])) == 4, sealed.get("lan"))
        check("...and they are the first four, in order",
              sealed.get("lan", [])[0].startswith("192.168.1."), sealed.get("lan"))
    finally:
        sharing.lan_addresses = real_addresses

    # A field that is sometimes "" is a field every reader has to special-case,
    # and the reader here is Java on a phone that ships separately.
    sharing._own_network = lambda a: False
    sharing._reply("a" * 43, "rid1234567", "queued")
    check("⭐ nothing to say -> the field is ABSENT, not empty",
          "lan" not in sealed, sealed)
    check("...and the verdict itself is unharmed", sealed.get("why") == "queued", sealed)
finally:
    sharing._seal = real_seal
    sharing._own_network = real
    sharing.urllib.request.urlopen = real_open


print("\n-- the pairing link still carries an address -----------------------")
# Regression: this is how the Windows sender has always learned where to go.
check("lan_ip() is non-empty on this machine", sharing.lan_ip() != "",
      sharing.lan_ip())

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
