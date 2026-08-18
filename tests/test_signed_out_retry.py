"""
The signed-out retry: a rejected session must not take public videos with it.

yt-dlp itself is stubbed here. What is under test is the decision - when the
retry fires, when it stays out of the way, and which error survives - not
whether yt-dlp works, which the other suites cover with real links.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-retry-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


class Fake(engine.DownloadManager):
    """Records every attempt and answers however the case needs."""

    def __init__(self, signed_out_works=True):
        super().__init__()
        self.calls = []
        self.signed_out_works = signed_out_works

    def _attempt(self, job, settings, client, with_cookies=True):
        self.calls.append(with_cookies)
        if with_cookies:
            job.sent_cookies = True
            job.status = "error"
            job.error = "ERROR: unable to download video data: HTTP Error 400"
            job.log = "yt-dlp ...\nHTTP Error 400: Bad Request"
            return False
        job.sent_cookies = False
        if self.signed_out_works:
            job.status = "done"
            job.filepath = str(SANDBOX / "ok.mp4")
            job.log = "yt-dlp (no cookies)"
            return True
        job.status = "error"
        job.error = "ERROR: Instagram sent an empty media response"
        job.log = "yt-dlp (no cookies)\nempty media response"
        return False


def rejected_job(url="https://www.instagram.com/reel/AAA/"):
    job = engine.Job(url=url, title=url)
    job.sent_cookies = True
    job.status = "error"
    job.error = "ERROR: unable to download video data: HTTP Error 400"
    job.log = "HTTP Error 400: Bad Request"
    return job


settings = dict(engine.DEFAULT_SETTINGS, download_dir=str(SANDBOX))

print("\n-- a rejected session is retried without it ------------------------")
man = Fake(signed_out_works=True)
job = rejected_job()
ok = man._signed_out_retry(job, settings)
check("the retry ran", man.calls == [False], str(man.calls))
check("it reported success", ok is True)
check("the job is done", job.status == "done")
check("the log says why it was signed out",
      "downloaded signed out instead" in job.log)

print("\n-- when signed out does not help, the honest error survives --------")
man = Fake(signed_out_works=False)
job = rejected_job()
ok = man._signed_out_retry(job, settings)
check("it reported failure", ok is False)
check("the session-rejection error is what the user keeps",
      "HTTP Error 400" in job.error, job.error[:60])

print("\n-- it stays out of the way when it has no business running ---------")
man = Fake()
job = rejected_job()
job.sent_cookies = False
check("no cookies were sent, so nothing to leave out",
      man._signed_out_retry(job, settings) is False and man.calls == [])

man = Fake()
job = rejected_job()
job.error = "ERROR: Video unavailable"
job.log = "Video unavailable"
check("an ordinary failure is not a session problem",
      man._signed_out_retry(job, settings) is False and man.calls == [])

man = Fake()
job = rejected_job()
job.cancelled = True
check("a cancelled job is not retried",
      man._signed_out_retry(job, settings) is False and man.calls == [])

print("\n-- 403 and login_required count too -------------------------------")
for text in ("HTTP Error 403: Forbidden", "ERROR: login_required",
             "Requested content is not available"):
    man = Fake()
    job = rejected_job()
    job.error = "ERROR: " + text
    job.log = text
    check(f"treated as a session refusal: {text[:28]}",
          man._signed_out_retry(job, settings) is True)

print("\n-- the full ladder still runs in order -----------------------------")
order = []


class Ladder(engine.DownloadManager):
    def _run_engine(self, job, settings):
        order.append("engine")
        job.status = "error"
        job.error = "ERROR: HTTP Error 400"
        job.log = "HTTP Error 400"
        job.sent_cookies = True
        return False

    def _signed_out_retry(self, job, settings):
        order.append("signed-out")
        return False

    def _second_door(self, job, settings):
        order.append("door")


ladder = Ladder()
ladder._run_job(engine.Job(url="https://www.instagram.com/reel/AAA/"))
check("engine, then signed-out, then the door",
      order == ["engine", "signed-out", "door"], str(order))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
