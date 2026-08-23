"""Which way a share came in, and why it has to be written down.

"Nothing left the building" is something Riplox says about itself. Until this,
there was no way to see it: a link handed straight to this PC over the home
Wi-Fi and a link that went out to a relay and back arrived looking exactly the
same, and the log kept no note of the difference.

That is also what made the local path untestable. A link arrives either way -
without this, "did it use the local network?" has no answer anywhere in the
app, so the feature could be silently broken and nothing would show it.

⚠ The trap this file exists to catch: `via` is threaded through three
functions, and Python will not complain about a missing one until the moment
it runs. py_compile passes on a NameError. This same file has been bitten by
exactly that before.
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


noted = []
real_note = sharing._note
real_load = sharing.load
real_save = sharing.save
real_sink = sharing._sink


def fake_note(entry):
    noted.append(entry)


def fake_save(data):
    # ⚠ A link and a piece of text are logged by two different routes. Text
    # goes through _note(); a link from a known device is written straight into
    # data["log"] and saved. Watching only _note() would have made every link
    # case look broken while the code was fine - which is exactly what the
    # first version of this test reported.
    log = data.get("log") or []
    if log:
        noted.append(log[0])


DEVICE = {"id": "dev1", "name": "Test phone", "limits": {}}
STATE = {"devices": [DEVICE], "log": [], "recent": {}, "seen": {},
         "room": "a" * 32, "invite": None, "pending": [], "spent": [],
         "revoked": []}


def fake_load():
    return json.loads(json.dumps(STATE))


try:
    sharing._note = fake_note
    sharing.load = fake_load
    sharing.save = fake_save
    sharing._sink = lambda *a, **k: None

    print("\n-- a link handed straight to this PC -------------------------------")
    noted.clear()
    sharing._accept(DEVICE, {"kind": "link", "url": "https://example.com/one"}, "lan")
    check("it was logged", len(noted) == 1, len(noted))
    check("⭐ and marked as arriving on the local network",
          noted and noted[0].get("via") == "lan", noted[:1])

    print("\n-- the same link, the ordinary way ---------------------------------")
    noted.clear()
    sharing._accept(DEVICE, {"kind": "link", "url": "https://example.com/two"}, "relay")
    check("marked as the relay", noted and noted[0].get("via") == "relay", noted[:1])

    print("\n-- called the way handle() calls it --------------------------------")
    # The default matters: anything that reaches _accept without saying how it
    # arrived came off the relay, and calling it local would be a claim the app
    # cannot support.
    noted.clear()
    sharing._accept(DEVICE, {"kind": "link", "url": "https://example.com/three"})
    check("⭐ an unstated road is 'relay', never 'lan'",
          noted and noted[0].get("via") == "relay", noted[:1])

    print("\n-- text takes the same two roads -----------------------------------")
    # ⚠ This is the one py_compile cannot check. `via` is threaded into
    # _take_text separately, and a missing parameter there is a NameError that
    # only appears when somebody actually sends text.
    noted.clear()
    try:
        sharing._take_text("a secret", DEVICE, "lan")
        crashed = ""
    except (NameError, TypeError) as exc:
        crashed = type(exc).__name__ + ": " + str(exc)
    check("⭐ sending text does not raise NameError", crashed == "", crashed)
    check("...and it is marked local too",
          noted and noted[0].get("via") == "lan", noted[:1])

    noted.clear()
    try:
        sharing._take_text("a secret", DEVICE)
        crashed = ""
    except (NameError, TypeError) as exc:
        crashed = type(exc).__name__ + ": " + str(exc)
    check("text with an unstated road does not raise either", crashed == "", crashed)
    check("...and defaults to relay", noted and noted[0].get("via") == "relay", noted[:1])

finally:
    sharing._note = real_note
    sharing.load = real_load
    sharing.save = real_save
    sharing._sink = real_sink


print("\n-- nothing that already worked lost anything ------------------------")
# The entry still has to carry everything the window draws a row from.
entry = noted[0] if noted else {}
for field in ("id", "at", "state", "from", "device"):
    check(f"a text entry still carries '{field}'", field in entry, sorted(entry))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
