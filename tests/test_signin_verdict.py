"""A sign-in that captured nothing for its own site must not report success.

The profile is shared by every site, so reading it always returns something.
This is the check that a TikTok sign-in which never completed is called a
failure rather than "Signed in".
"""
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox_flow_"))
engine.data_dir = lambda: SANDBOX
import cookies

fails = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


YT = [{"domain": ".youtube.com", "name": "SID", "value": "y", "path": "/"},
      {"domain": ".google.com", "name": "SAPISID", "value": "g", "path": "/"}]
TT = [{"domain": ".tiktok.com", "name": "sessionid", "value": "t", "path": "/"}]

# Stand in for the browser, so no window opens and no site is contacted.
class FakeProc:
    def wait(self):
        return 0


# The profile is passed in now, because a second account for the same site
# signs in through one of its own - a shared profile would just show the first
# account instead of a login page.
cookies._launch_login = lambda exe, url, profile=None: FakeProc()
cookies.profile_dir = lambda: SANDBOX / "profile"

print("1. TikTok sign-in that never completed - profile has only YouTube")
cookies._read_cookies = lambda exe, profile=None: list(YT)
flow = cookies._Flow()
flow._run(Path("chrome.exe"), "https://www.tiktok.com/login", "tiktok")
check("reported as failed", flow.step == "failed", f"step={flow.step}")
check("says which site was missing", "TikTok" in (flow.error or ""), flow.error)
check("nothing was saved for tiktok", not cookies.site_file("tiktok").exists())

print("2. TikTok sign-in that did complete")
cookies._read_cookies = lambda exe, profile=None: YT + TT
flow = cookies._Flow()
flow._run(Path("chrome.exe"), "https://www.tiktok.com/login", "tiktok")
check("reported as done", flow.step == "done", f"step={flow.step} {flow.error}")
check("tiktok.dat written", cookies.site_file("tiktok").exists())
check("youtube.dat written too", cookies.site_file("youtube").exists())

print("3. an empty profile is still the old, plain failure")
cookies._read_cookies = lambda exe, profile=None: []
flow = cookies._Flow()
flow._run(Path("chrome.exe"), "https://www.reddit.com/login/", "reddit")
check("reported as failed", flow.step == "failed")
check("message is about no cookies at all", "No cookies were found" in (flow.error or ""),
      flow.error)

shutil.rmtree(SANDBOX, ignore_errors=True)
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
