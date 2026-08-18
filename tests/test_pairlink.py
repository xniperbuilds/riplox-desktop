"""riploxsend:// pairing link: is it read correctly, and is the code still
single-use? Nothing here talks to the relay - only the parsing is checked."""
import sys
from pathlib import Path

# Worked out from this file's own location, not written down. The Windows
# sender moved out of its own folder into send-windows/ inside this repository,
# and the path written here left the test importing a module that had gone -
# which showed up as ModuleNotFoundError rather than as a failing check.
SRC = Path(__file__).resolve().parent.parent / "send-windows" / "src"
sys.path.insert(0, str(SRC))

import send

sys.modules.setdefault("webview", type(sys)("webview"))     # no GUI in a test
sys.modules["webview"].create_window = lambda *a, **k: None
sys.modules["webview"].start = lambda *a, **k: None
import app

fails = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


ROOM = "a" * 22
KEY = "k" * 43
CODE = "123456"
TYPED = f"{ROOM}.{KEY}.{CODE}"

print("1. the argument is recognised, and nothing else is")
check("finds the link", app.pair_argument(["RiploxSend.exe",
      f"riploxsend://pair?c={TYPED}"]) == f"riploxsend://pair?c={TYPED}")
check("ignores a plain argument", app.pair_argument(["RiploxSend.exe", "--tray"]) == "")
check("ignores an http link", app.pair_argument(["x.exe", "https://example.com"]) == "")
check("case does not matter",
      app.pair_argument(["x.exe", f"RIPLOXSEND://pair?c={TYPED}"]) != "")

print("2. the code inside it is read")
from urllib.parse import parse_qs, urlsplit
q = parse_qs(urlsplit(f"riploxsend://pair?c={TYPED}&l=192.168.1.5%3A50550").query)
invite = send.read_invite(q["c"][0])
check("room, key and code all read",
      invite and invite["room"] == ROOM and invite["key"] == KEY
      and invite["code"] == CODE, str(invite))
check("the LAN address survives the link", q.get("l") == ["192.168.1.5:50550"])

print("3. rubbish is refused rather than half-accepted")
check("empty", send.read_invite("") is None)
check("two parts only", send.read_invite("aaaa.bbbb") is None)
check("short room", send.read_invite("short.key.code") is None)

print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
