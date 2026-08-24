"""No question Riplox asks may come from the browser.

WebView2 titles a native confirm() or alert() with the address it is serving
from - "127.0.0.1:65172 says" - so a routine "delete this list?" arrives
looking like a browser security warning with a port number in it. Riplox has
its own ask/tell box for exactly this reason.

The box was built and two callers were left behind. They were found by a user
pressing Delete all, on the eve of a launch, and seeing the address.

⚠️ window.confirm CANNOT simply be redirected the way window.alert was: it
answers synchronously and the real box resolves a promise. So each caller has
to be rewritten by hand, which is precisely why one gets missed. This is the
check that notices.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything that ends up in front of a user.
SHIPPED = [
    ROOT / "src" / "static" / "js" / "app.js",
    ROOT / "src" / "templates" / "index.html",
    ROOT / "browser-extension" / "background.js",
    ROOT / "browser-extension" / "content.js",
    ROOT / "browser-extension" / "popup.js",
]

NATIVE = re.compile(r"(?<![\w.])(?:window\.)?(confirm|prompt)\s*\(")

# The one deliberate use: alert is redirected to Riplox's own box on startup.
ALLOWED = re.compile(r"window\.alert\s*=")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


def strip(text):
    """Comments describe the problem on purpose; they are not calls."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


print("\n-- nothing shipped may call the browser's own dialogs --------------")
for path in SHIPPED:
    if not path.is_file():
        check(path.name + " exists", False, "missing")
        continue
    body = strip(path.read_text(encoding="utf-8"))
    hits = []
    for found in NATIVE.finditer(body):
        line = body[:found.start()].count("\n") + 1
        hits.append("line %d: %s" % (line, body.splitlines()[line - 1].strip()[:60]))
    check("%-20s no native confirm/prompt" % path.name, not hits,
          " · ".join(hits[:3]))

print("\n-- and alert is redirected rather than used ------------------------")
app_js = (ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")
check("⭐ window.alert is pointed at Riplox's own box", bool(ALLOWED.search(app_js)))

bare = [m for m in re.finditer(r"(?<![\w.=])alert\s*\(", strip(app_js))]
check("...and nothing calls a bare alert()", not bare,
      "%d call(s)" % len(bare))

print("\n-- the box itself is still there ----------------------------------")
page = (ROOT / "src" / "templates" / "index.html").read_text(encoding="utf-8")
check("the dialog markup is in the page", 'id="xdlg"' in page)
for part in ("xdlgTitle", "xdlgMsg", "xdlgOk", "xdlgCancel"):
    check("  it still has " + part, 'id="%s"' % part in page)
check("ask() is defined for callers to use", "function ask(" in app_js)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
sys.exit(1 if FAIL else 0)
