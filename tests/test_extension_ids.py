"""Both extension ids reach Riplox, and the installer agrees with the app.

Chrome gives the same code a different id depending on how it arrives: the
folder in this repository loaded unpacked, and the Chrome Web Store listing.
The native host names the ones it will speak to, so an id missing from that
list is an extension that installs cleanly, looks right, and cannot reach
Riplox at all - which is what anyone installing from the store got on the day
the listing went live.

⚠️ The list is written twice - once by the app and once by the installer's own
Pascal, into the same file. Two copies of one fact is exactly the shape that
drifts, and nothing else here would notice: the app would be right, the
installed manifest would be wrong, and only a real install would show it.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
import app                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


UNPACKED = "eceoennjnigbildembfcpdlmiaahocnm"
STORE = "hacbllnggmnnajhobdgcklhdmaoddnnh"

print("-- the app allows both " + "-" * 46)
check("the unpacked folder's id is allowed", UNPACKED in app.EXTENSION_IDS,
      app.EXTENSION_IDS)
check("the store listing's id is allowed", STORE in app.EXTENSION_IDS)
check("an id is 32 lowercase letters",
      all(re.fullmatch(r"[a-p]{32}", i) for i in app.EXTENSION_IDS),
      [len(i) for i in app.EXTENSION_IDS])
check("EXTENSION_ID still resolves, for anything that asks for one",
      app.EXTENSION_ID in app.EXTENSION_IDS, app.EXTENSION_ID)
check("the store link carries the store id", STORE in app.STORE_URL,
      app.STORE_URL)

# ⚠️ The one that shipped broken. /api/open-url keeps an allowlist, on purpose
# - a page that talked its way past the token must not be able to use Riplox as
# a launcher - and the listing was not on it. So the rail's button asked, the
# route answered 400, the click handler ignored the answer, and pressing it did
# nothing and said nothing. Asking is not opening.
import engine                                               # noqa: E402
check("the app will actually open the listing",
      engine.STORE_PAGE in engine.OPENABLE,
      "%d allowed address(es)" % len(engine.OPENABLE))
check("and that is the same address the page is given",
      app.STORE_URL == engine.STORE_PAGE, app.STORE_URL[-46:])

print("\n-- and the installer writes the same two " + "-" * 28)
iss = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8",
                                                   errors="replace")
defines = dict(re.findall(r'#define\s+(\w+)\s+"([^"]*)"', iss))
check("installer knows the unpacked id",
      defines.get("ExtensionId") == UNPACKED, defines.get("ExtensionId"))
check("installer knows the store id",
      defines.get("StoreExtensionId") == STORE, defines.get("StoreExtensionId"))

# The line it actually writes into native-host.json, not just the defines.
json_line = next((l for l in iss.splitlines() if "allowed_origins" in l), "")
check("both are in the manifest it writes",
      "{#ExtensionId}" in json_line and "{#StoreExtensionId}" in json_line,
      json_line.strip()[:96])

# ⚠️ The drift check. If either file gains or loses an id, these stop matching.
from_iss = {defines.get("ExtensionId"), defines.get("StoreExtensionId")}
check("the app and the installer allow exactly the same set",
      from_iss == set(app.EXTENSION_IDS),
      "app %s / installer %s" % (sorted(app.EXTENSION_IDS), sorted(from_iss)))

print("\n-- What's next does not still promise it " + "-" * 28)
# ⚠️ The roadmap listed "The extension in the Chrome Web Store" under "being
# worked on" for the whole day it was live. That panel's only job is to be
# evidence the app is still being made; an entry that already shipped is
# evidence of the opposite, and it is the kind of staleness nobody notices
# because nothing breaks.
markup = (ROOT / "src" / "templates" / "index.html").read_text(encoding="utf-8")
# ⚠️ Comments stripped first. The note explaining why the entry was removed
# says "Chrome Web Store" itself, so the first version of this check read that
# and reported the entry still present - a check failing on its own fix, for
# the second time today. What matters is what renders.
markup = re.sub(r"\{#.*?#\}", " ", markup, flags=re.S)
markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)
start = markup.find('id="roadmap"')
roadmap = markup[start:markup.find("</ul>", start)] if start > 0 else ""
check("the roadmap exists", bool(roadmap))
check("and no longer lists the store extension as upcoming",
      "Chrome Web Store" not in roadmap,
      "still there" if "Chrome Web Store" in roadmap else "gone")
# It is in the release notes instead, which is where a shipped thing belongs.
check("What's new claims it instead",
      "Chrome Web Store" in (ROOT / "src" / "whatsnew.json")
      .read_text(encoding="utf-8"))

print("\n-- the finish page offers the button " + "-" * 32)
check("there is a task for it", 'Name: "extension"' in iss)
check("and it opens the listing", defines.get("StoreUrl", "").endswith(STORE),
      defines.get("StoreUrl"))
check("shellexec, because a URL is not a program",
      any("StoreUrl" in l and "shellexec" in l for l in iss.splitlines()))
# Ticked by default: a task is unticked only when it says so.
task = next((l for l in iss.splitlines() if 'Name: "extension"' in l), "")
check("ticked by default", "unchecked" not in task, task.strip()[:80])

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
