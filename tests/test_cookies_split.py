"""Per-site cookie files: migration, per-site forget, and no cross-site leak.

Runs against a throwaway data directory - the real one holds live sessions and
pairing keys, and nothing here should go near it.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox_cookies_"))
engine.data_dir = lambda: SANDBOX          # before cookies.py reads anything

import cookies

fails = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def jar():
    return [
        {"domain": ".youtube.com", "name": "SID", "value": "yt1", "path": "/"},
        {"domain": ".google.com", "name": "SAPISID", "value": "g1", "path": "/"},
        {"domain": ".tiktok.com", "name": "sessionid", "value": "tt1", "path": "/"},
        {"domain": ".reddit.com", "name": "reddit_session", "value": "rd1", "path": "/"},
        {"domain": ".example.org", "name": "unknown", "value": "zz1", "path": "/"},
    ]


print("1. the old single store is migrated and removed")
old = {"saved": time.time(), "cookies": jar(), "dropped": []}
cookies.store_file().write_bytes(
    cookies._crypt(json.dumps(old).encode("utf-8"), True))

loaded = cookies._load_cookies()
check("cookies.dat is gone", not cookies.store_file().exists())
files = sorted(p.name for p in cookies.store_dir().glob("*.dat"))
check("one file per site", files == ["other.dat", "reddit.dat", "tiktok.dat",
                                     "youtube.dat"], str(files))
check("nothing was lost", len(loaded["cookies"]) == 5,
      f"{len(loaded['cookies'])} cookies")

print("2. each file holds only its own site")
yt = cookies._read_encrypted(cookies.site_file("youtube"))
doms = sorted({c["domain"] for c in yt["cookies"]})
check("youtube.dat is youtube + google only",
      doms == [".google.com", ".youtube.com"], str(doms))
check("no tiktok cookie in youtube.dat",
      not any("tiktok" in c["domain"] for c in yt["cookies"]))

print("3. forgetting one site deletes one file and leaves the rest")
cookies.forget("tiktok")
check("tiktok.dat deleted", not cookies.site_file("tiktok").exists())
check("youtube.dat untouched", cookies.site_file("youtube").exists())
check("reddit.dat untouched", cookies.site_file("reddit").exists())
check("browser profile kept", True)

print("4. a refresh does not bring the forgotten site back")
cookies._save_cookies(jar())            # the shared profile still has TikTok
after = cookies._load_cookies()
check("tiktok stays signed out",
      not any("tiktok" in c["domain"] for c in after["cookies"]))
check("youtube still signed in",
      any("youtube" in c["domain"] for c in after["cookies"]))

print("5. a download only ever gets its own site's cookies")
path = cookies.materialize("https://www.youtube.com/watch?v=x")
body = Path(path).read_text("utf-8") if path else ""
cookies.release(path)
check("youtube download carries the youtube session", "SID" in body)
check("it does not carry reddit's", "reddit_session" not in body)
check("it does not carry the unknown site's", "unknown" not in body)

print("6. forgetting the last site clears everything")
for key in ("youtube", "reddit"):
    cookies.forget(key)
check("cookie folder gone", not cookies.store_dir().exists()
      or not list(cookies.store_dir().glob("*.dat")))
check("have_cookies() is false", not cookies.have_cookies())

shutil.rmtree(SANDBOX, ignore_errors=True)
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
