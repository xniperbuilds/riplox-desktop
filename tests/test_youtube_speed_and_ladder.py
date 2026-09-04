"""
Three things reported from real use on YouTube, and one measurement.

  * a channel link never finished reading
  * "best available" hit an https error halfway and came back a fraction of
    the size it should have been
  * one video took far longer to read than it needed to

The first and third are the same cause: a channel is walked a page at a time,
every page is a request, and each request paid a politeness pause meant for
bursts. Measured on one channel, same link, same machine: **86.7s** as it was,
**38.2s** without the pause, **3.3s** for the first hundred. The read is now
capped and does not pay the pause.

The second is a different cause and the worse one: the client ladder exists to
get past a refusal, and the clients below the first rung are only ever offered
small formats. A broken connection was being treated as a refusal, so a Wi-Fi
hiccup spent a rung and quietly bought a 360p file.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-yt-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + str(detail)[:90] if detail else ""))


SETTINGS = dict(engine.DEFAULT_SETTINGS, polite_mode=True)


print("\n-- the pause is for bursts, not for one read -------------------------")

polite = engine._base_args(SETTINGS, None)
quick = engine._base_args(SETTINGS, None, polite=False)

check("an ordinary run still pauses between requests",
      "--sleep-requests" in polite)
check("a read somebody is waiting for does not", "--sleep-requests" not in quick)
check("and nothing else about the run changes",
      [a for a in polite if a not in ("--sleep-requests", "0.75")] == quick)

off = engine._base_args(dict(SETTINGS, polite_mode=False), None)
check("with politeness turned off it is absent either way",
      "--sleep-requests" not in off)


print("\n-- a long list is read a page deep, not to the end -------------------")

seen = {}


def fake_run(args, timeout=None, **kw):
    seen["args"] = list(args)

    class Out:
        returncode = 0
        stdout = '{"_type":"playlist","title":"T","entries":[]}'
        stderr = ""
    return Out()


real_run = engine._run
engine._run = fake_run

engine.analyze("https://www.youtube.com/@someone/videos", SETTINGS)
check("the first read asks for one page",
      "--playlist-end" in seen["args"]
      and seen["args"][seen["args"].index("--playlist-end") + 1]
      == str(engine.ANALYZE_LIMIT))
check("and does not pay the pause", "--sleep-requests" not in seen["args"])

engine.analyze("https://www.youtube.com/@someone/videos", SETTINGS, limit=0)
check("asking for all of it takes the cap off",
      "--playlist-end" not in seen["args"])


def entries(n):
    return ('{"_type":"playlist","title":"T","entries":['
            + ",".join('{"url":"https://x/%d","title":"v%d"}' % (i, i)
                       for i in range(n)) + "]}")


def with_body(body):
    def run(args, timeout=None, **kw):
        class Out:
            returncode = 0
            stdout = body
            stderr = ""
        return Out()
    engine._run = run


# Exactly as many as were asked for cannot be told apart from "there are more",
# so the screen is told there may be, rather than either lie.
with_body(entries(engine.ANALYZE_LIMIT))
full = engine.analyze("https://www.youtube.com/@someone/videos", SETTINGS)
check("a list that filled the page says there may be more", full["more"] is True)

with_body(entries(13))
short = engine.analyze("https://www.youtube.com/@someone/videos", SETTINGS)
check("a short list does not", short["more"] is False)

with_body('{"_type":"playlist","title":"T","playlist_count":100,"entries":['
          + ",".join('{"url":"https://x/%d"}' % i for i in range(100)) + "]}")
exact = engine.analyze("https://www.youtube.com/@someone/videos", SETTINGS)
check("and a site that gives a real total settles it",
      exact["more"] is False and exact["total"] == 100)

engine._run = real_run


print("\n-- a broken connection is not a refusal ------------------------------")

for text in ("ERROR: unable to download video data: <urlopen error [Errno 11001]>",
             "Read timed out.", "Connection reset by peer",
             "SSL: UNEXPECTED_EOF_WHILE_READING", "Unable to connect to proxy",
             "HTTPSConnectionPool ... Read timed out"):
    check(f"network: {text[:38]}", engine._is_network_trouble(text))

for text in ("Sign in to confirm you're not a bot",
             "HTTP Error 429: Too Many Requests",
             "This video is private", "login_required"):
    check(f"not network: {text[:38]}", not engine._is_network_trouble(text))


print("\n-- so it does not spend a rung on one ---------------------------------")

man = engine.DownloadManager()
man._network_went = lambda job: False        # the network is fine; it blipped


def drive(fail_with, succeed_on=None):
    """Run the ladder with a stand-in attempt, and record which clients it used."""
    used = []

    def attempt(job, settings, client):
        used.append(client)
        job.log = fail_with
        if succeed_on is not None and len(used) >= succeed_on:
            job.status = "done"
            return True
        job.status = "error"
        return False

    man._attempt = attempt
    job = engine.Job("https://www.youtube.com/watch?v=abc", quality="max")
    job.log = ""
    man._run_engine(job, SETTINGS)
    return used


used = drive("ERROR: Read timed out while downloading fragment 12")
check("a broken connection is tried again on the same client",
      used[:engine._SAME_RUNG_TRIES] == [""] * engine._SAME_RUNG_TRIES, used)
check("and only then does the ladder move on",
      used[engine._SAME_RUNG_TRIES] == engine._RETRY_CLIENTS[1], used)

used = drive("ERROR: Sign in to confirm you're not a bot")
# A refusal is exactly what the other clients are for, so it moves at once.
check("a refusal moves down a rung immediately",
      used == engine._RETRY_CLIENTS, used)

used = drive("ERROR: Read timed out", succeed_on=2)
check("and a retry that works stops there", len(used) == 2, used)


print("\n-- 'best available' answered with something small ---------------------")

man2 = engine.DownloadManager()
engine.best_height = lambda url, settings, cookie=None: 2160


def short_check(quality, height, attempt=2):
    job = engine.Job("https://www.youtube.com/watch?v=abc", quality=quality)
    job.attempt = attempt
    job.height = height
    return man2._quality_short(job, SETTINGS)


check("4K asked, 360p given - said out loud", short_check("2160", 360))
# This is the one that used to slip through: the check stopped at 360p, so a
# request for 4K answered with 720p went green and said nothing.
check("4K asked, 720p given - now said too", short_check("2160", 720))
check("4K asked, 1080p given - said too", short_check("2160", 1080))
check("4K asked, 2160p given - nothing to say", not short_check("2160", 2160))
check("720p asked, 720p given - nothing to say", not short_check("720", 720))
# Sites do hand back the rung just below; asking again over that would spend a
# listing to say almost nothing.
check("1080p asked, 1000p given - near enough", not short_check("1080", 1000))
check("the main route's own answer is never questioned",
      not short_check("2160", 360, attempt=1))


print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
