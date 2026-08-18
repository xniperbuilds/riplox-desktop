"""
Who you download from, counted out of history — no new request anywhere.

The interesting rules are that a name is filed under the site it was LAST
seen on, and that entries with no uploader recorded (everything downloaded
before that field existed) are skipped rather than shown as blanks.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-acct-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


HISTORY = [
    {"title": "a", "uploader": "alice", "site": "YouTube", "when": "2026-08-10T10:00:00"},
    {"title": "b", "uploader": "alice", "site": "YouTube", "when": "2026-08-12T10:00:00"},
    {"title": "c", "uploader": "bob",   "site": "TikTok",  "when": "2026-08-14T10:00:00"},
    {"title": "d", "uploader": "alice", "site": "TikTok",  "when": "2026-08-15T10:00:00"},
    {"title": "e", "uploader": "",      "site": "YouTube", "when": "2026-08-11T10:00:00"},
    {"title": "f", "site": "YouTube", "when": "2026-08-09T10:00:00"},        # older rows
    {"title": "g", "uploader": "  carol  ", "site": "X", "when": "2026-08-13T10:00:00"},
]

for row in HISTORY:
    engine.add_history(row)

got = engine.accounts()
by_name = {a["name"]: a for a in got}

print("\n-- counted from what is already recorded --------------------------")
check("three names found", len(got) == 3, str([a["name"] for a in got]))
check("alice counted three times", by_name["alice"]["count"] == 3)
check("bob counted once", by_name["bob"]["count"] == 1)

print("\n-- rows with no uploader are skipped, not shown blank -------------")
check("no empty name in the list", "" not in by_name)
check("a missing field does not become a name",
      all(a["name"].strip() for a in got))
check("whitespace is trimmed off a name", "carol" in by_name, str(list(by_name)))

print("\n-- filed under where they were LAST seen --------------------------")
check("⭐ alice moved to TikTok and is filed there",
      by_name["alice"]["site"] == "TikTok", by_name["alice"]["site"])
check("bob stays on TikTok", by_name["bob"]["site"] == "TikTok")
check("carol is on X", by_name["carol"]["site"] == "X")

print("\n-- newest activity first -----------------------------------------")
check("alice is first (most recent download)", got[0]["name"] == "alice",
      got[0]["name"])
check("the last-seen date travels with it",
      by_name["alice"]["last"].startswith("2026-08-15"),
      by_name["alice"]["last"])

print("\n-- an empty history is not an error ------------------------------")
engine.clear_history()
check("nothing recorded means an empty list", engine.accounts() == [])

print("\n-- the endpoint hands it over ------------------------------------")
import app as riplox_app                                    # noqa: E402
for row in HISTORY:
    engine.add_history(row)
with riplox_app.app.test_request_context():
    body = riplox_app.api_accounts().get_json()
check("the API answers", body.get("ok") is True)
check("...with the same three", len(body.get("accounts") or []) == 3,
      str([a["name"] for a in body.get("accounts") or []]))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
