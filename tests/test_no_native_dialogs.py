"""Riplox must never show the browser's own confirm box.

The window is WebView2. When something calls the native confirm(), WebView2
puts the address it is serving from in the title:

    127.0.0.1:65172 says
    Delete every row on this page?

A desktop app does not say that. It reads as a browser security warning, it
puts a port number in front of someone who has no use for it, and it tells
anyone looking at a screenshot exactly what the window really is.

The app grew its own ask/tell box for this reason - and then two callers were
left on the native one for months. window.alert was redirected at the time;
window.confirm could not be, because confirm() answers immediately and the
real box resolves a promise, so those callers had to be rewritten by hand and
two were missed. Nazim found one by pressing "Delete all" days before launch.

⚠️ This is why the check is a sweep and not two assertions: the bug was never
"this line is wrong", it was "nobody was watching this whole class of line".
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything that reaches a user. dist/ and build/ are output - scanning them
# would just re-report whatever src already said, or worse, pass because a
# stale copy happened to be clean.
WHERE = ("src/static", "src/templates", "browser-extension", "send-windows/src")

NATIVE = re.compile(r"(?<![\w.])(?:window\.)?(confirm|alert|prompt)\s*\(")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:90]) if detail else ""))


def without_comments(text: str) -> str:
    """
    Comments explaining this very problem must not count as the problem.

    Both files that carry the fix also carry a paragraph about "127.0.0.1
    says", and a check that flagged those would be one nobody could ever get
    to green.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"(?m)^\s*\*.*$", "", text)
    return text


def shipped_files():
    for part in WHERE:
        base = ROOT / part
        if not base.exists():
            continue
        for found in sorted(base.rglob("*")):
            if found.suffix in (".js", ".html") and "node_modules" not in str(found):
                yield found


print("\n-- no native dialog survives anywhere that ships -------------------")

files = list(shipped_files())
check("there are files to check at all", len(files) >= 5, str(len(files)) + " files")

found_any = []
for one in files:
    body = without_comments(one.read_text(encoding="utf-8", errors="replace"))
    for match in NATIVE.finditer(body):
        line = body[:match.start()].count("\n") + 1
        found_any.append(one.relative_to(ROOT).as_posix() + ":" + str(line)
                         + " " + match.group(1) + "()")

check("⭐ no confirm(), alert() or prompt() in anything that ships",
      not found_any, " · ".join(found_any[:3]))

# Named on purpose. These two were the ones that got left behind, and a test
# that only swept would go green again if somebody re-added exactly these.
app_js = without_comments((ROOT / "src/static/js/app.js").read_text(encoding="utf-8"))
check("the failed list asks with Riplox's own box",
      'ask("Delete every row on this page?' in app_js
      or "ask(\"Delete every row on this page?" in app_js,
      "failedClear")
check("removing an account asks with Riplox's own box",
      'ask("Remove this account?' in app_js, "account remove")


print("\n-- and the replacement is still there ------------------------------")

page = (ROOT / "src/templates/index.html").read_text(encoding="utf-8")
check("the dialog element is in the page", 'id="xdlg"' in page)
for part in ("xdlgTitle", "xdlgMsg", "xdlgOk", "xdlgCancel", "xdlgInput"):
    check("  it still has " + part, 'id="' + part + '"' in page)

check("ask() and tell() are defined",
      "function ask(message" in app_js and "function tell(message" in app_js)
check("⭐ window.alert is still redirected to it",
      re.search(r"window\.alert\s*=", app_js) is not None)
check("Escape cancels and Enter confirms",
      '"Escape"' in app_js and '"Enter"' in app_js)


print("\n-- nothing shows the user an address or a port ---------------------")

# The proxy settings box legitimately shows 127.0.0.1 as an EXAMPLE of what a
# user types for their own proxy. That is the one allowed mention, and it is
# inside <code>, not a message about Riplox itself.
for one in files:
    body = without_comments(one.read_text(encoding="utf-8", errors="replace"))
    for match in re.finditer(r"127\.0\.0\.1|localhost", body):
        line_no = body[:match.start()].count("\n") + 1
        line = body.splitlines()[line_no - 1]
        allowed = "inline" in line or "proxy" in line.lower() or "socks" in line.lower()
        check(one.name + ":" + str(line_no) + " mentions an address - is it the proxy example?",
              allowed, line.strip()[:70])

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
sys.exit(1 if FAIL else 0)
