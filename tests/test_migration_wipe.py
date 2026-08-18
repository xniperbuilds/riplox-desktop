"""
The stale-migration wipe: a saved sign-in must survive being read.

Reproduces the real machine exactly - a leftover pre-split cookies.dat that
knows only about Google/YouTube, sitting next to per-site files that include
a fresh Instagram session - and checks that simply reading the store no
longer destroys it.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-wipe-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import cookies as cs                                        # noqa: E402
import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def cookie(domain, name="sid"):
    return {"domain": domain, "name": name, "value": "x", "path": "/",
            "secure": True, "httpOnly": True, "expires": 2000000000}


def build_the_real_situation():
    """Old blob that predates the split, plus newer per-site sessions."""
    shutil.rmtree(cs.store_dir(), ignore_errors=True)
    cs.store_dir().mkdir(parents=True, exist_ok=True)

    # Today's sessions, saved after the split.
    cs._write_encrypted(cs.site_file("instagram"),
                        {"saved": time.time(),
                         "cookies": [cookie(".instagram.com", "sessionid"),
                                     cookie(".instagram.com", "csrftoken")]})
    cs._write_encrypted(cs.site_file("tiktok"),
                        {"saved": time.time(), "cookies": [cookie(".tiktok.com")]})
    cs._write_encrypted(cs.site_file("youtube"),
                        {"saved": time.time(), "cookies": [cookie(".youtube.com")]})

    # The leftover from before the split - months old, Google/YouTube only.
    cs._write_encrypted(cs.store_file(),
                        {"saved": time.time() - 6 * 86400,
                         "cookies": [cookie(".google.com"), cookie(".youtube.com")]})


def sites():
    return sorted(p.stem for p in cs.store_dir().glob("*.dat"))


print("\n-- the situation on the real machine ------------------------------")
build_the_real_situation()
check("the old pre-split blob is present", cs.store_file().exists())
check("and so are today's sessions", "instagram" in sites() and "tiktok" in sites(),
      str(sites()))

print("\n-- reading the store must not destroy anything --------------------")
cs.status()                                   # exactly what opening Settings does
check("⭐ the Instagram session survived a read", "instagram" in sites(), str(sites()))
check("⭐ the TikTok session survived too", "tiktok" in sites())
check("the stale blob was cleared away", not cs.store_file().exists())

jar = cs._read_encrypted(cs.site_file("instagram")).get("cookies") or []
check("...and it is still the real session, not a rebuilt one",
      any(c.get("name") == "sessionid" for c in jar), f"{len(jar)} cookies")

print("\n-- repeatedly, because that is how it was hit ---------------------")
for i in range(5):
    cs.status()
    cs.have_cookies()
    cs.materialize("https://www.instagram.com/reel/x/")
check("still there after ten more reads", "instagram" in sites(), str(sites()))

print("\n-- and if the delete keeps failing, it is still harmless ----------")
build_the_real_situation()
real_unlink = Path.unlink


def refuse(self, *a, **k):
    if self.name == "cookies.dat":
        raise OSError("locked")
    return real_unlink(self, *a, **k)


Path.unlink = refuse
try:
    for _ in range(3):
        cs.status()
finally:
    Path.unlink = real_unlink
check("⭐ a blob that cannot be deleted no longer wipes anything",
      "instagram" in sites() and "tiktok" in sites(), str(sites()))
check("the stale blob is still there (delete failed, as arranged)",
      cs.store_file().exists())

print("\n-- a genuine first-time upgrade still works ----------------------")
shutil.rmtree(cs.store_dir(), ignore_errors=True)
cs.store_dir().mkdir(parents=True, exist_ok=True)
cs._write_encrypted(cs.store_file(),
                    {"saved": time.time(),
                     "cookies": [cookie(".youtube.com"), cookie(".tiktok.com")]})
cs.status()
check("an install with only the old file is migrated",
      "youtube" in sites() and "tiktok" in sites(), str(sites()))
check("...and the old file is gone afterwards", not cs.store_file().exists())

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
