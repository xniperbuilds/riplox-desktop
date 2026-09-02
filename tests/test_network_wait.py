"""A download waits for the network instead of failing because of it.

Reported from real use: a download was running, the network was switched, and
it failed - then would not download at all. Re-run afterwards with the same
link and the same flags, it worked. So the failure was never real; everything
after it was ours.

Why it never recovered: _run_engine walks a ladder of retry clients waiting
2s, 6s, 10s between them. **The whole ladder is spent inside about twenty
seconds** - shorter than a Wi-Fi-to-mobile switch takes. Every attempt was
burned while there was no network, the job landed in Failed, and nothing picked
it up again: AUTO_RETRY_AFTER only fires for errors clears_on_its_own()
recognises, and that list is two Instagram phrases.

⚠️ The regression that matters most is at the bottom of this file: a genuine
failure, with the network up, must still fail. Waiting for ever is worse than
failing, and a probe that answers "the internet is fine" says nothing about
whether one site is refusing.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-network-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


MANAGER = engine.DownloadManager()


def a_job(**kw):
    job = engine.Job(url="https://www.youtube.com/watch?v=x")
    job.status = "downloading"
    job.started_on = "192.168.1.10"
    for key, value in kw.items():
        setattr(job, key, value)
    return job


def pretend(network=True, address="192.168.1.10"):
    engine.network_ok = lambda force=False: network
    engine.here_now = lambda: address


REAL_NET, REAL_HERE = engine.network_ok, engine.here_now


print("\n-- the probe is cached, because _next_job asks constantly ----------")
engine.network_ok, engine.here_now = REAL_NET, REAL_HERE
engine.network_ok(force=True)
stamp = engine._net_last[0]
for _ in range(200):
    engine.network_ok()
check("⭐ 200 calls did not cause 200 probes",
      engine._net_last[0] == stamp, "stamp moved")
check("...and forcing it does re-probe",
      engine.network_ok(force=True) is not None)


print("\n-- the network went away -------------------------------------------")
try:
    pretend(network=False)
    job = a_job()
    check("⭐ it is put back on the queue, not failed",
          MANAGER._network_went(job) is True and job.status == "queued",
          job.status)
    check("...with no error text, because nothing went wrong",
          job.error == "", job.error)
    check("...and its attempt count reset, so it gets a full ladder next time",
          job.attempt == 0)
    check("the wait is counted", job.net_waits == 1)

    print("\n-- the network changed underneath it ----------------------------")
    pretend(network=True, address="10.0.0.5")          # a different network
    job = a_job()
    check("⭐ a different address is treated the same way",
          MANAGER._network_went(job) is True and job.status == "queued",
          job.status)

    pretend(network=True, address="")                  # cannot tell
    job = a_job()
    check("an address it cannot read is not called a change",
          MANAGER._network_went(job) is False, job.status)

    print("\n-- it does not wait for ever -----------------------------------")
    pretend(network=False)
    job = a_job(net_waits=MANAGER._NET_WAIT_CAP)
    check("⭐ past the cap it fails honestly rather than looping",
          MANAGER._network_went(job) is True and job.status == "error",
          job.status)
    check("...and the reason says what happened",
          "network" in job.error.lower() and "retry" in job.error.lower(),
          job.error)

    print("\n-- cancel still wins -------------------------------------------")
    pretend(network=False)
    job = a_job(cancelled=True)
    check("a cancelled job is not quietly requeued",
          MANAGER._network_went(job) is False)

    print("\n-- nothing starts while there is no network ---------------------")
    pretend(network=False)
    MANAGER._jobs.clear()
    MANAGER._order[:] = []
    waiting = engine.Job(url="https://example.com/v")
    waiting.status = "queued"
    MANAGER._jobs[waiting.id] = waiting
    MANAGER._order.append(waiting.id)
    check("⭐ the queue hands out nothing", MANAGER._next_job() is None)

    pretend(network=True)
    handed = MANAGER._next_job()
    check("...and starts again the moment it is back",
          handed is not None and handed.id == waiting.id,
          handed.status if handed else None)

    print("\n-- ⚠ THE REGRESSION: a real failure must still fail --------------")
    print("")
    print("-- and the ladder actually asks ---------------------------------")
    # Everything above tests _network_went in isolation. If nobody CALLS it the
    # tests stay green and the bug comes straight back, so this drives the real
    # ladder with every attempt failing.
    tried = []

    def never_works(self, job, settings, client):
        tried.append(client)
        job.log = "connection reset by peer"      # transient: the ladder would go on
        return False

    real_attempt = engine.DownloadManager._attempt
    try:
        engine.DownloadManager._attempt = never_works

        pretend(network=False)
        tried.clear()
        job = a_job(status="queued")
        MANAGER._run_engine(job, {})
        check("with no network it stops after ONE attempt, not the whole ladder",
              len(tried) == 1, "%d attempts" % len(tried))
        check("...and the job is queued again rather than failed",
              job.status == "queued", job.status)

        pretend(network=True, address="192.168.1.10")
        tried.clear()
        job = a_job(status="queued")
        MANAGER._run_engine(job, {})
        check("with the network up it still walks the whole ladder",
              len(tried) > 1, "%d attempts" % len(tried))
    finally:
        engine.DownloadManager._attempt = real_attempt

    pretend(network=True, address="192.168.1.10")      # network fine, same one
    job = a_job()
    check("⭐ network up and unchanged -> not our problem, let it fail",
          MANAGER._network_went(job) is False, job.status)
    check("...the job is left exactly as it was",
          job.status == "downloading" and job.net_waits == 0,
          "%s / %s" % (job.status, job.net_waits))

    print("")
    print("-- and a probe that is simply WRONG must not block for ever ------")
    # The dangerous failure of this whole feature: some networks block
    # 1.1.1.1 and 8.8.8.8 outright. On such a machine a probe that is trusted
    # absolutely would stop Riplox downloading anything, permanently and
    # silently - far worse than the outage it was built for.
    import time as _time
    pretend(network=False)
    MANAGER._jobs.clear()
    MANAGER._order[:] = []
    stuck = engine.Job(url="https://example.com/v")
    stuck.status = "queued"
    MANAGER._jobs[stuck.id] = stuck
    MANAGER._order.append(stuck.id)

    engine._net_offline_since[0] = 0.0
    check("it does hold back at first", MANAGER._next_job() is None)

    # Two minutes of claimed silence later.
    engine._net_offline_since[0] = _time.monotonic() - engine._NET_DOUBT_AFTER - 1
    let_through = MANAGER._next_job()
    check("one job is let through to settle it with real traffic",
          let_through is not None, "still blocked")

    pretend(network=True)
    MANAGER._next_job()
    check("and the doubt clock resets once the network answers again",
          engine._net_offline_since[0] == 0.0, engine._net_offline_since[0])

    check("more than one host is asked, so one blocked address is not a verdict",
          len(engine._NET_HOSTS) >= 3, engine._NET_HOSTS)
finally:
    engine.network_ok, engine.here_now = REAL_NET, REAL_HERE
    shutil.rmtree(SANDBOX, ignore_errors=True)

print("")
print("-- a media URL that went stale mid-download ------------------------")
# Reported on an 8K video: "max" chose 4320p AV1 at 50 Mbps - three and a half
# gigabytes - and the download outlived the URL YouTube had issued for it.
# Pressing Retry resumed it from the .part file, so the failure was never real:
# it needed TIME rather than another client, and the twenty-second ladder gives
# it neither. AUTO_RETRY_AFTER (5 minutes, then 15) is the right tool and was
# not being reached.
for text, want, why in [
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden",
     True, "the URL went stale - a later go resumes it"),
    ("unable to download video data: HTTP Error 404",
     True, "same thing, whatever the code"),
    ("ERROR: Private video. Sign in if you have been granted access",
     False, "private stays private, however long you wait"),
    ("ERROR: Video unavailable",
     False, "gone is gone"),
    ("HTTP Error 403: Forbidden",
     False, "a bare 403 is a site refusing - _AUTH_REFUSED owns that"),
    ("ERROR: Sign in to confirm you are not a bot",
     False, "that has its own handling"),
]:
    got = engine.clears_on_its_own(text)
    check("%-5s  %s" % (got, why), got is want, text[:56])


print("")
print("-- the connection going mid-download, from a real user's log --------")
# Reported as "https wala error", twice, both times with the video half done.
# Everything below is that job's own stderr. Seventeen fragment workers each
# lost the name at once, and the line yt-dlp ends on is about joining fragments
# it never wrote - the consequence, not the cause, and the only line anything
# here was reading.
REAL = (
    "ERROR: \n"
    "[download] Got error: HTTPSConnection(host='rr1---sn-xcvoxoxu-aixs."
    "googlevideo.com', port=443): Failed to resolve 'rr1---sn-xcvoxoxu-aixs."
    "googlevideo.com' ([Errno 11001] getaddrinfo failed). Giving up after 5 "
    "retries\n"
    "ERROR: \n"
    "[download] Got error: HTTPSConnection(host='rr1---sn-xcvoxoxu-aixs."
    "googlevideo.com', port=443): Failed to resolve 'rr1---sn-xcvoxoxu-aixs."
    "googlevideo.com' ([Errno 11001] getaddrinfo failed). Giving up after 5 "
    "retries\n"
    "ERROR: Unable to download video: [Errno 2] No such file or directory: "
    "'C:\\Users\\x\\Videos\\Riplox\\Youtube\\M [udVT7MdN8OM] 1078p "
    "[max] Riplox.f616.mp4.part-Frag3'"
)

said = engine._clean_error(REAL)
check("⭐ the user is told something, at all",
      said.strip() != "", repr(said))
check("⭐ ...and it is the connection, not the fragment it could not join",
      "connection" in said.lower() and "frag" not in said.lower(), said)
check("...and it says what is kept, because that is the worry",
      "kept" in said.lower(), said)

print("")
print("-- a bare 'ERROR:' line must not eat the message --------------------")
# The empty ERROR: line came first, so the loop stopped on it and returned "".
# The Failed page then said "No reason was recorded." over a recorded reason.
check("⭐ an empty ERROR: is stepped over, not returned",
      engine._clean_error("ERROR: \nERROR: Video unavailable")
      == "This video is unavailable or was removed.",
      engine._clean_error("ERROR: \nERROR: Video unavailable"))
check("...and a lone empty one still says something",
      engine._clean_error("ERROR: ").strip() != "")

print("")
print("-- ⚠ what counts as the network leaving, and what does NOT ----------")
for text, want, why in [
    ("[Errno 11001] getaddrinfo failed", True, "the name would not resolve"),
    ("Failed to resolve 'rr1---sn-x.googlevideo.com'", True, "same, in words"),
    ("[Errno 101] Network is unreachable", True, "no network to use"),
    ("Failed to establish a new connection", True, "never got out"),
    ("OSError: [WinError 10051] A socket operation was attempted to an "
     "unreachable network", True, "Windows' way of saying it"),
    # ⚠️ The dangerous half. Each of these is something a SITE does to a
    # request it is refusing, and every one of them appears in a download that
    # is genuinely dead. Counting them as an outage would requeue it forty
    # times and call that patience.
    ("Giving up after 5 retries", False, "a site can refuse five times too"),
    ("Max retries exceeded with url", False, "same - urllib3 says this at 403"),
    ("[Errno 104] Connection reset by peer", False,
     "a refusal, and _TRANSIENT already gives it another rung"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden",
     False, "the stale-URL case - clears_on_its_own owns that one"),
    ("ERROR: Video unavailable", False, "gone is gone"),
    ("", False, "nothing said is not an outage"),
]:
    got = engine.network_lost(text)
    check("%-5s  %s" % (got, why), got is want, text[:56])

print("")
print("-- ⭐ the probe is green, the Wi-Fi is the same, and it STILL waits --")
# The whole reason this came back. By the time an attempt fails the connection
# is usually back, so the probe says yes and the address is unchanged - both of
# the old signals miss it, and the job went to Failed for ever.
real_settings_fn = engine.load_settings
try:
    engine.network_ok = lambda force=False: True          # network is back
    engine.here_now = lambda: "192.168.1.10"              # same Wi-Fi
    engine.load_settings = lambda *a, **k: {}             # going out directly

    job = a_job(log=REAL)
    check("⭐ the log is believed over the probe",
          MANAGER._network_went(job) is True and job.status == "queued",
          job.status)
    check("...with no error text, because nothing went wrong",
          job.error == "", job.error)

    job = a_job(log="ERROR: Video unavailable")
    check("⭐ and a real failure with the network up still fails",
          MANAGER._network_went(job) is False, job.status)

    # ⚠️ A proxy hostname that will not resolve says exactly what a lost
    # connection says. Waiting that one out would hide the only thing the user
    # can actually fix, and would spend forty attempts doing it.
    engine.load_settings = lambda *a, **k: {"proxy": "http://badproxy.lan:8080"}
    job = a_job(log=REAL)
    check("⭐ with a proxy set, the same lines are NOT called an outage",
          MANAGER._network_went(job) is False, job.status)
    check("...so the job fails and says so, instead of waiting",
          job.status == "downloading" and job.net_waits == 0,
          "%s / %s" % (job.status, job.net_waits))
finally:
    engine.network_ok, engine.here_now = REAL_NET, REAL_HERE
    engine.load_settings = real_settings_fn

print("")
print("-- ⭐ and nothing runs on top of a job that was put back ------------")
# _run_engine returning False means "not downloaded", and _run_job could not
# tell the two reasons apart: refused by the site, or waiting for the network.
# So the second door ran over a waiting job and set its own status - failing
# the job a few seconds after the wait had saved it.
opened = []
real_engine = engine.DownloadManager._run_engine
real_door = engine.DownloadManager._second_door
real_space, real_settings = engine.free_space, engine.load_settings
try:
    engine.free_space = lambda *_: 10 ** 12
    engine.load_settings = lambda *a, **k: {"download_dir": ""}
    engine.DownloadManager._second_door = \
        lambda self, job, settings: opened.append(job.id)

    def requeued(self, job, settings):
        job.status = "queued"            # what _network_went does
        return False

    engine.DownloadManager._run_engine = requeued
    job = a_job(status="downloading")
    MANAGER._run_job(job)
    check("⭐ the second door is not opened over a waiting job",
          opened == [], opened)
    check("⭐ ...and the job is still queued, not failed",
          job.status == "queued", job.status)

    def refused(self, job, settings):
        job.status = "error"             # what a real refusal does
        job.error = "This video is unavailable or was removed."
        return False

    engine.DownloadManager._run_engine = refused
    opened.clear()
    job = a_job(status="downloading")
    MANAGER._run_job(job)
    check("⭐ but a genuinely refused job still gets the second door",
          opened == [job.id], opened)
finally:
    engine.DownloadManager._run_engine = real_engine
    engine.DownloadManager._second_door = real_door
    engine.free_space, engine.load_settings = real_space, real_settings
    shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
