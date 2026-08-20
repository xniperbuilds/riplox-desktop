"""
Text sent from a phone: kept sealed, shown as dots, handed over once.

What people actually send this way is a licence key, a Wi-Fi code, a password.
Every case here exists because that is true - the feature would be easy and
wrong if it were treated as ordinary notes.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-sharetext-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import sharing                                              # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + str(detail) if detail else ""))


DEVICE = {"id": "dev1", "name": "Phone", "limits": {}}
SECRET = "XNIPER-9K2M-LIC-2026"


def fresh():
    data = sharing.load()
    data["log"] = []
    sharing.save(data)


def only_entry():
    return sharing.load().get("log", [])[0]


print("\n-- a key arrives, and is not on disk in the clear -----------------")
fresh()
answer = sharing._accept(DEVICE, {"text": SECRET})
check("it is taken", answer in ("queued", "held"), answer)
entry = only_entry()
check("it is filed as text", entry.get("kind") == "text")
check("the plain text is NOT in the entry",
      SECRET not in json.dumps(entry), "entry keys: %s" % sorted(entry))
raw_file = (engine.data_dir() / "share.json").read_text(encoding="utf-8")
check("and NOT anywhere in share.json", SECRET not in raw_file)
check("only its length is recorded", entry.get("chars") == len(SECRET))

print("\n-- the screen never receives the sealed text ----------------------")
shown = sharing._for_screen(sharing.load()["log"])
check("the sealed blob is stripped", "sealed" not in shown[0], sorted(shown[0]))
check("but it is still shown as an entry", shown[0].get("kind") == "text")
check("with its length", shown[0].get("chars") == len(SECRET))

print("\n-- copying gives it once, and takes it away -----------------------")
got = sharing.take_text(entry["id"])
check("the text comes back exactly", got == SECRET, repr(got)[:40])
check("and it is gone from the log", sharing.load().get("log") == [])
check("a second press gets nothing", sharing.take_text(entry["id"]) == "")

print("\n-- a link still wins wherever there is one ------------------------")
fresh()
sharing._accept(DEVICE, {"url": "https://example.com/v", "text": "ignore me"})
entry = only_entry()
check("it is a link, not text", entry.get("kind") != "text", entry.get("kind"))
check("and the text was not kept", "ignore me" not in json.dumps(sharing.load()))

print("\n-- nothing is ever silently cut ----------------------------------")
fresh()
too_long = "k" * (sharing.TEXT_MAX + 1)
answer = sharing._accept(DEVICE, {"text": too_long})
check("over the limit is refused, not trimmed", answer == "text-too-long", answer)
check("and nothing was stored", sharing.load().get("log") == [])

fresh()
answer = sharing._accept(DEVICE, {"text": "k" * sharing.TEXT_MAX})
check("exactly at the limit is accepted", answer in ("queued", "held"), answer)

# Bytes, not characters: the limit exists because of what the relay carries.
fresh()
urdu = "ک" * (sharing.TEXT_MAX // 2)        # 2 bytes each = exactly the cap
check("a multi-byte string is measured in bytes",
      len(urdu.encode("utf-8")) == sharing.TEXT_MAX, len(urdu.encode("utf-8")))
check("and is accepted at the cap",
      sharing._accept(DEVICE, {"text": urdu}) in ("queued", "held"))
fresh()
check("one character over the cap in bytes is refused",
      sharing._accept(DEVICE, {"text": urdu + "ک"}) == "text-too-long")

print("\n-- an empty share is still an empty share ------------------------")
fresh()
check("blank text is not a message", sharing._accept(DEVICE, {"text": "   "}) == "bad-link")
check("no text and no link either", sharing._accept(DEVICE, {}) == "bad-link")

print("\n-- a secret does not sit around ----------------------------------")
fresh()
sharing._accept(DEVICE, {"text": SECRET})
data = sharing.load()
data["log"][0]["expires"] = time.time() - 1          # its time is up
sharing.save(data)
check("expired text is not shown", sharing._for_screen(sharing.load()["log"]) == [])
check("expired text cannot be copied",
      sharing.take_text(sharing.load()["log"][0]["id"]) == "")
check("and it is swept off disk", sharing.load().get("log") == [])

print("\n-- approvals apply to text as much as to links -------------------")
fresh()
settings = engine.load_settings()
settings["share_approve"] = True
engine.save_settings(settings)
answer = sharing._accept(DEVICE, {"text": SECRET})
check("it waits", answer == "held", answer)
check("and is marked waiting", only_entry().get("state") == "waiting")
check("an unapproved secret cannot be copied",
      sharing.take_text(only_entry()["id"]) == "")
check("and it is still there, not consumed", len(sharing.load().get("log", [])) == 1)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
