"""
Three per-download choices: subtitles only, every thumbnail, live from start.

Each has to reach the command when asked for, stay out of it when not, and be
refused when it arrives as something other than a plain yes.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-opts-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


man = engine.DownloadManager()
SETTINGS = dict(engine.DEFAULT_SETTINGS, download_dir=str(SANDBOX))


def command(opts=None, settings=None):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality="1080",
                     opts=opts or {})
    return man.build_args(job, settings or SETTINGS, "", None)


print("\n-- nothing asked for, nothing added ------------------------------")
plain = command()
for flag in ("--skip-download", "--write-all-thumbnails", "--live-from-start"):
    check(f"absent by default: {flag}", flag not in plain)

print("\n-- subtitles only ------------------------------------------------")
subs = command({"subs_only": True})
check("it skips the video", "--skip-download" in subs)
check("it asks for subtitles", "--write-subs" in subs)
check("automatic ones too", "--write-auto-subs" in subs)
check("with a language", "--sub-langs" in subs)
check("defaulting to en", "en" in subs, subs[subs.index("--sub-langs") + 1])

subs_lang = command({"subs_only": True, "sub_langs": "ur,en"})
check("a chosen language wins",
      subs_lang[subs_lang.index("--sub-langs") + 1] == "ur,en")

print("\n-- every thumbnail -----------------------------------------------")
thumb = command({"thumb_all": True})
check("it asks for all of them", "--write-all-thumbnails" in thumb)
check("...and not the single one as well", "--write-thumbnail" not in thumb,
      "both would be contradictory")

single = command(settings=dict(SETTINGS, write_thumbnail=True))
check("the plain setting still works alone", "--write-thumbnail" in single)
both = command({"thumb_all": True}, dict(SETTINGS, write_thumbnail=True))
check("asking for all overrides the setting",
      "--write-all-thumbnails" in both and "--write-thumbnail" not in both)

print("\n-- live from the beginning ---------------------------------------")
live = command({"live_from_start": True})
check("the flag is sent", "--live-from-start" in live)

print("\n-- all three at once ---------------------------------------------")
every = command({"subs_only": True, "thumb_all": True, "live_from_start": True})
for flag in ("--skip-download", "--write-all-thumbnails", "--live-from-start"):
    check(f"still present together: {flag}", flag in every)

print("\n-- only a plain yes gets through ---------------------------------")
for value in ("yes", 1, [], {"a": 1}, None, False, ""):
    got = engine.clean_opts({"subs_only": value})
    truthy = bool(value)
    check(f"subs_only={value!r} -> {'kept' if truthy else 'dropped'}",
          ("subs_only" in got) == truthy, str(got))

check("an unknown option is dropped entirely",
      engine.clean_opts({"nonsense": True, "subs_only": True}) == {"subs_only": True},
      str(engine.clean_opts({"nonsense": True, "subs_only": True})))

print("\n-- they belong to one job, never to Settings ----------------------")
before = engine.load_settings()
command({"subs_only": True, "thumb_all": True})
after = engine.load_settings()
check("Settings is untouched by a per-job choice", before == after)
for key in ("subs_only", "thumb_all", "live_from_start"):
    check(f"{key} is not a setting", key not in engine.DEFAULT_SETTINGS)

print("\n-- and they survive the queue being saved and restored ------------")
job = man.add(url="https://example.com/live", quality="best",
              opts={"subs_only": True, "live_from_start": True})
man._save()
fresh = engine.DownloadManager()
fresh.restore()
back = [j for j in fresh._jobs.values() if j.url == "https://example.com/live"]
check("the job came back", len(back) == 1, str(len(back)))
if back:
    check("with its choices intact",
          back[0].opts.get("subs_only") and back[0].opts.get("live_from_start"),
          str(back[0].opts))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
