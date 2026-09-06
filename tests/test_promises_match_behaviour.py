"""The shipped promises, checked against what the app actually does.

Every other test here asks whether the code works. This one asks whether the
sentences shipped alongside it are still true, because that is what went wrong:

  * TERMS section 5 said the engine was downloaded "when you press Update in
    Settings" and that the app "never installs anything on its own" - months
    after both had deliberately changed. Section 5 is the document that tells
    somebody what Riplox does on their network.
  * The same week, a roadmap entry WAS moved the day its feature shipped, with
    a comment in the template explaining why a stale entry does harm. One
    surface had a rule attached to it; the others did not.

So the rule gets attached here. A default that turns an "only if you ask"
sentence into a lie now fails a test instead of shipping.

    python tests/test_promises_match_behaviour.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
import engine                                               # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


def flat(text: str) -> str:
    """One long lower-case line, so a claim split over two lines still matches."""
    return re.sub(r"\s+", " ", text).lower()


TERMS = flat((ROOT / "TERMS.txt").read_text(encoding="utf-8-sig"))
README = flat((ROOT / "README.md").read_text(encoding="utf-8"))
WINDOW = flat((ROOT / "src" / "templates" / "index.html").read_text(encoding="utf-8"))
HELPER = flat((ROOT / "src" / "potoken.py").read_text(encoding="utf-8"))
D = engine.DEFAULT_SETTINGS

print("-- the engine updates itself, so nothing may say it does not " + "-" * 12)

# engine_auto True means api_check_engine fetches a newer engine by itself.
auto_engine = D.get("engine_auto") is True
check("engine_auto is on by default", auto_engine, D.get("engine_auto"))

if auto_engine:
    check("TERMS does not tie the engine update to pressing Update",
          "when you press update in settings, downloading a new copy" not in TERMS)
    check("TERMS does not claim nothing is installed on its own",
          "it never installs anything on its own" not in TERMS)
    check("the window does not say nothing is fetched until Update is pressed",
          "nothing is fetched until update is pressed" not in WINDOW)

print("\n-- the helper installs itself, so nothing may say it is opt-in " + "-" * 10)

auto_helper = D.get("potoken") is True
check("the YouTube helper is on by default", auto_helper, D.get("potoken"))

if auto_helper:
    check("TERMS does not say the helper is downloaded only if you enable it",
          "if you choose to enable it, downloading an optional helper" not in TERMS)
    check("README does not say the helper is off by default",
          "it is off by default" not in README)
    check("README does not say it is downloaded only if you turn it on",
          "if you turn it on, riplox downloads" not in README)
    check("potoken.py does not claim it is opt-in and off by default",
          "opt-in and off by default" not in HELPER)
    check("potoken.py does not claim it is never fetched behind your back",
          "never fetched behind the user's back" not in HELPER)

print("\n-- the LAN listener exists, so nothing may say no port is opened " + "-" * 8)

# sharing.py binds LAN_PORT on 0.0.0.0 whenever Sharing is on.
opens_port = "lan_port" in flat((ROOT / "src" / "sharing.py").read_text(encoding="utf-8"))
check("sharing.py has a LAN listener", opens_port)
if opens_port:
    check("README does not say the PC never opens a port",
          "never opens a port" not in README,
          "it listens on 47811 whenever Sharing is on")

print("\n-- the library is capped, so nothing may promise every download " + "-" * 9)

capped = int(getattr(engine, "HISTORY_LIMIT", 0) or 0)
check("history has a limit", capped > 0, capped)
if capped:
    check("the window does not promise every finished download",
          "every finished download as a spreadsheet" not in WINDOW,
          f"history keeps {capped}")
    check("the window does not promise every video you have downloaded",
          "every video you have downloaded" not in WINDOW)
    check("README does not promise every finished file",
          "| every finished file" not in README)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED:", name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
