"""
Run one casegrid case for Riplox 1.6.0, in a sandbox of its own.

Called as a subprocess with the case's factor values as JSON on argv, so each
case gets a fresh LOCALAPPDATA and cannot be passed state by the one before
it - a case that passes on leftovers is a false pass.

Prints one `RESULT ok` or `RESULT fail` line, then the evidence. The evidence
is what was observed, not what was expected.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import types
from pathlib import Path

# The case comes in as a file rather than on argv: a shell that rewrites
# quotes turns a JSON argument into a parse error, which is a bad way to find
# out that a test harness is fragile.
CASE = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
V = CASE.get("values", {})
SANDBOX = Path(tempfile.mkdtemp(prefix="cg-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

EV = []
BAD = []


def note(line):
    EV.append(str(line))


def want(name, ok, detail=""):
    if not ok:
        BAD.append(f"{name}: {detail}")
    note(("  ok   " if ok else "  BAD  ") + name + (f"  [{detail}]" if detail else ""))


# --------------------------------------------------------------------------
# Set the world up before anything is imported that reads it
# --------------------------------------------------------------------------

def write_settings(kind):
    import engine
    path = engine.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "fresh":
        return
    if kind == "corrupt":
        path.write_text("{ this is not json", encoding="utf-8")
        return
    # A 1.5.0 file: the keys that existed then, and none that did not.
    path.write_text(json.dumps({
        "download_dir": str(SANDBOX / "Downloads"),
        "default_quality": "max",
        "watch": True,
        "watch_ack": True,
        "watch_hours": 24,
        "auto_paste": True,
        "polite_mode": True,
    }), encoding="utf-8")


def write_watchfile(kind, following):
    import engine
    path = engine.data_dir() / "watch.json"
    if kind == "absent":
        return
    old = {
        "id": "aaaaaa111111",
        "url": "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
        "kind": "channel", "title": "A channel", "uploader": "",
        "thumbnail": "", "added": 1, "checked": 1, "paused": False,
        "error": "", "botcheck": False, "known": ["v1", "v2"], "fresh": [],
    }
    if kind == "v160_items":
        old.update({"feed": "https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw",
                    "feed_fail": 0, "full_checked": 1, "auto": False, "opts": {}})
    if following == "on_engine":
        old["feed"] = ""
    if following == "on_auto":
        old["auto"] = True
    path.write_text(json.dumps({"items": [old]}), encoding="utf-8")


def write_drop(kind):
    import dropfolder
    where = dropfolder.folder()
    where.mkdir(parents=True, exist_ok=True)
    if kind == "on_valid":
        (where / "links.txt").write_text(
            "https://a.test/1\nhttps://b.test/2\n", encoding="utf-8")
    if kind == "on_junk":
        (where / "junk.txt").write_text("not a link at all\n", encoding="utf-8")


# --------------------------------------------------------------------------

import engine                                               # noqa: E402
import watch                                                # noqa: E402
import convert                                              # noqa: E402
import dropfolder                                           # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect"

write_settings(V.get("settings", "fresh"))
write_watchfile(V.get("watchfile", "absent"), V.get("following", "off"))

S = engine.load_settings()
if V.get("following") == "on_auto":
    engine.save_settings({"watch_auto": True})
if V.get("drop") in ("on_valid", "on_junk"):
    engine.save_settings({"drop_on": True})
    write_drop(V["drop"])

# --------------------------------------------------------------------------
# settings + the migration that could never fire
# --------------------------------------------------------------------------

kind = V.get("settings", "fresh")
mins = watch.minutes()
if kind == "fresh":
    want("fresh install takes the new default", mins == 60, f"minutes={mins}")
    want("downloading on its own is off", S.get("watch_auto") is False)
    want("the drop folder is off", S.get("drop_on") is False)
elif kind == "v150":
    # The bug: load_settings fills defaults first, so the old key could never
    # be seen missing and a 24-hour choice became hourly in silence.
    want("an upgraded 1.5.0 file keeps its 24 hours", mins == 1440, f"minutes={mins}")
    want("and does not arrive with downloading switched on",
         S.get("watch_auto") is False)
    want("and its old settings survive", S.get("default_quality") == "max")
else:
    want("an unreadable settings file does not stop the app", mins == 60,
         f"minutes={mins}")
    want("and every new key still has a value",
         S.get("drop_on") is False and S.get("watch_auto") is False)

# --------------------------------------------------------------------------
# the followed list, old and new
# --------------------------------------------------------------------------

wf = V.get("watchfile", "absent")
st = watch.state()
if wf == "absent":
    want("no list means no items", st["items"] == [])
else:
    want("the list opens", len(st["items"]) == 1, f"items={len(st['items'])}")
    item = watch.load()["items"][0]
    for key in ("feed", "feed_fail", "full_checked", "auto", "opts"):
        want(f"an old item gains {key}", key in item)
    want("and keeps what it already knew",
         item["known"] == ["v1", "v2"], item.get("known"))
    pub = st["items"][0]
    want("the screen can draw it", "every" in pub and "feed" in pub)

# --------------------------------------------------------------------------
# how often, and the floor that does not move
# --------------------------------------------------------------------------

fol = V.get("following", "off")
if fol == "on_feed":
    want("something with a feed follows the setting",
         watch.interval({"feed": "f", "opts": {}}) == mins * 60)
elif fol == "on_engine":
    got = watch.interval({"feed": "", "opts": {}})
    want("something without a feed keeps the six-hour floor",
         got == max(mins, watch.SLOW_FLOOR) * 60, f"seconds={got}")
elif fol == "on_auto":
    fake = types.SimpleNamespace(added=[])

    class M:
        def add(self, **kw):
            fake.added.append(kw)
            return types.SimpleNamespace(id="1", status="queued")

    sys.modules["app"] = types.SimpleNamespace(manager=M(), tray_app=None)
    found = [{"id": str(n), "url": f"https://x/{n}", "title": f"v{n}",
              "thumbnail": ""} for n in range(9)]
    item = {"auto": True, "opts": {}}
    got = watch._auto_download(item, found, engine.load_settings())
    want("both switches on, so it downloads", len(got) > 0, f"took={len(got)}")
    want("never more than the cap in one check",
         len(got) == watch.AUTO_MAX_PER_CHECK, f"took={len(got)}")
    off = watch._auto_download({"auto": False, "opts": {}}, found,
                               engine.load_settings())
    want("and nothing at all with the channel switch off", off == [])

# --------------------------------------------------------------------------
# the drop folder, including two copies of the app sweeping it at once
# --------------------------------------------------------------------------

drop = V.get("drop", "off")
if drop != "off":
    taken = []
    lock = threading.Lock()

    def sink(url, quality="", opts=None):
        with lock:
            taken.append(url)

    dropfolder.set_sink(sink)
    if V.get("copies") == "two":
        # Two copies of Riplox watch the same folder. If both read a file
        # before either renames it, the same link is queued twice.
        threads = [threading.Thread(target=dropfolder.sweep) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    else:
        dropfolder.sweep()

    if drop == "on_valid":
        want("the links were queued", len(taken) >= 2, f"queued={taken}")
        want("and none of them twice",
             len(taken) == len(set(taken)), f"queued={taken}")
        want("the file was renamed rather than deleted",
             (dropfolder.folder() / "links.txt.done").exists()
             and not (dropfolder.folder() / "links.txt").exists())
    else:
        want("a file with no links queued nothing", taken == [], f"queued={taken}")
        want("and was set aside with the reason",
             (dropfolder.folder() / "junk.txt.bad").exists())

    taken.clear()
    dropfolder.sweep()
    want("a second sweep does not read it again", taken == [], f"queued={taken}")
else:
    want("the folder is not swept while it is off",
         engine.load_settings().get("drop_on") is False)

# --------------------------------------------------------------------------
# what a broken connection costs
# --------------------------------------------------------------------------

trouble = V.get("trouble", "none")
if trouble in ("network_blip", "refused"):
    man = engine.DownloadManager()
    man._network_went = lambda job: False
    used = []
    text = ("ERROR: Read timed out" if trouble == "network_blip"
            else "ERROR: Sign in to confirm you're not a bot")

    def attempt(job, settings, client):
        used.append(client)
        job.log = text
        job.status = "error"
        return False

    man._attempt = attempt
    job = engine.Job("https://www.youtube.com/watch?v=abc",
                     quality=V.get("quality", "max"))
    job.log = ""
    man._run_engine(job, engine.load_settings())
    if trouble == "network_blip":
        # The bug: a Wi-Fi hiccup spent a client rung, and those rungs only
        # carry small formats, so "best available" came back 360p.
        want("a broken connection is retried on the same client",
             used[:engine._SAME_RUNG_TRIES] == [""] * engine._SAME_RUNG_TRIES,
             f"clients={used[:5]}")
    else:
        want("a refusal moves down a rung at once",
             used == engine._RETRY_CLIENTS, f"clients={used}")
elif trouble == "network_gone":
    man = engine.DownloadManager()
    seen = {"asked": 0}

    def gone(job):
        seen["asked"] += 1
        return True                      # requeued

    man._network_went = gone

    def attempt(job, settings, client):
        job.log = "ERROR: Read timed out"
        job.status = "error"
        return False

    man._attempt = attempt
    job = engine.Job("https://www.youtube.com/watch?v=abc", quality="max")
    job.log = ""
    man._run_engine(job, engine.load_settings())
    want("a network that left is handled where it already was, not here",
         seen["asked"] >= 1, f"asked={seen['asked']}")

# --------------------------------------------------------------------------
# converting
# --------------------------------------------------------------------------

conv = V.get("convert", "none")
if conv != "none":
    source = SANDBOX / "clip.mp4"
    source.write_bytes(b"not really a video")
    info = {"vcodec": "h264", "codec": "aac", "has_video": True,
            "has_audio": True, "height": 1080, "duration": 60.0}
    fmt = {"audio": "mp3", "video": "mkv", "gif": "gif"}[conv]
    args = convert.build_args(source, SANDBOX / f"clip.{fmt}", fmt, "normal", info)
    if conv == "audio":
        want("the picture is dropped for audio", "-vn" in args)
    elif conv == "video":
        want("h264 into an mkv is a remux",
             args[args.index("-c:v") + 1] == "copy")
        want("and subtitles are carried, not dropped",
             "-c:s" in args)
    else:
        want("a gif is capped before the file is read",
             "-t" in args and args.index("-t") < args.index("-i"))
    want("the original is never the target",
         convert.free_name(SANDBOX / "clip.mp4") != source)

# --------------------------------------------------------------------------
# reading a link: what the command asks for
# --------------------------------------------------------------------------

link = V.get("link", "video")
seen_args = {}


def fake_run(args, timeout=None, **kw):
    seen_args["args"] = list(args)

    class Out:
        returncode = 0
        stdout = '{"_type":"playlist","title":"T","entries":[]}'
        stderr = ""
    return Out()


real_run = engine._run
engine._run = fake_run
URLS = {
    "video": "https://www.youtube.com/watch?v=abc",
    "playlist_small": "https://www.youtube.com/playlist?list=PLsmall",
    "playlist_exact_limit": "https://www.youtube.com/playlist?list=PLexact",
    "channel_root": "https://www.youtube.com/@someone",
    "channel_big": "https://www.youtube.com/@someone/videos",
    "non_youtube": "https://vimeo.com/12345",
}
try:
    engine.analyze(URLS[link], engine.load_settings())
    a = seen_args.get("args", [])
    # The two things that made a channel take a minute and a half.
    want("the first read is capped", "--playlist-end" in a)
    want("and does not pay the pause meant for bursts",
         "--sleep-requests" not in a)
finally:
    engine._run = real_run

# --------------------------------------------------------------------------
# a restart in the middle
# --------------------------------------------------------------------------

if V.get("restart") == "after_queue":
    man = engine.DownloadManager()
    when = engine.time.time() + 3600
    man.add(url="https://www.youtube.com/watch?v=later", quality="max",
            start_after=when)
    man._save()
    again = engine.DownloadManager()
    again.restore()
    jobs = again.snapshot()
    want("the queue comes back", len(jobs) == 1, f"jobs={len(jobs)}")
    kept = [j for j in again._jobs.values()]
    want("and a start time survives the restart",
         kept and abs(kept[0].start_after - when) < 2,
         f"start_after={kept[0].start_after if kept else None}")

print("RESULT " + ("ok" if not BAD else "fail"))
print("\n".join(EV))
if BAD:
    print("FAILURES:")
    for b in BAD:
        print("  " + b)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(0 if not BAD else 1)
