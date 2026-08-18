"""
Two settings that are easy to get subtly wrong.

  1  Written subtitles and machine-written ones are separate choices now.
     The default has to stay exactly what every previous version did, or a
     working setup changes underneath somebody on update.

  2  Every dub as its own file. The trap is not the queueing - it is the file
     name: two languages of one video are the same title at the same height,
     and until now the second one landed on top of the first in silence.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import engine                                                      # noqa: E402

PASS = FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def subs(kind=None):
    settings = {"write_subs": True, "sub_langs": "en"}
    if kind is not None:
        settings["sub_kind"] = kind
    return engine.extra_args(settings, "best")


print("which kind of subtitles")
both = subs("both")
check("both asks for written ones",
      "--write-subs" in both, " ".join(both))
check("both asks for machine ones", "--write-auto-subs" in both)

real = subs("real")
check("written-only asks for written", "--write-subs" in real)
check("written-only does NOT ask for machine ones",
      "--write-auto-subs" not in real, " ".join(real))

auto = subs("auto")
check("machine-only does NOT ask for written",
      "--write-subs" not in auto, " ".join(auto))
check("machine-only asks for machine ones", "--write-auto-subs" in auto)

print("\nnothing changes for somebody who never touches it")
check("no setting at all behaves exactly as before",
      subs(None) == both, "same flags as 'both'")
check("a nonsense value falls back to both", subs("sideways") == both)
check("subtitles off means no subtitle flags at all",
      not any(a.startswith("--write-subs") or a.startswith("--write-auto")
              for a in engine.extra_args({"write_subs": False}, "best")))
check("audio-only downloads never ask for subtitles",
      not any("sub" in a for a in
              engine.extra_args({"write_subs": True}, "mp3")))
check("the language list still goes through",
      "--sub-langs" in real and real[real.index("--sub-langs") + 1] == "en")


print("\none file per dub, and the names have to differ")


class Fake:
    """Just enough of a job for the name template."""

    def __init__(self, lang="", quality="1080"):
        self.opts = {"audio_lang": lang} if lang else {}
        self.quality = quality
        self.start = self.end = ""


manager = engine.DownloadManager.__new__(engine.DownloadManager)
settings = {"download_dir": r"C:\Downloads", "subfolder_per_site": False}

plain = manager._outtmpl(settings, Fake())
english = manager._outtmpl(settings, Fake("en"))
hindi = manager._outtmpl(settings, Fake("hi"))

check("an ordinary download keeps the name it always had",
      "[en]" not in plain and "%(height)sp" in plain, Path(plain).name)
check("a chosen dub is in the name", "[en]" in english, Path(english).name)
check("two dubs cannot land on the same file", english != hindi,
      Path(hindi).name)
check("mp3 carries the dub too", "[hi]" in
      manager._outtmpl(settings, Fake("hi", "mp3")),
      Path(manager._outtmpl(settings, Fake("hi", "mp3"))).name)

print("\nthe queue treats them as different downloads, not duplicates")
one = engine.Job("https://x/1", quality="1080", opts={"audio_lang": "en"})
two = engine.Job("https://x/1", quality="1080", opts={"audio_lang": "hi"})
same = engine.Job("https://x/1", quality="1080", opts={"audio_lang": "en"})
check("different languages are different jobs", one.opts != two.opts,
      f"{one.opts} vs {two.opts}")
check("the same language is still a duplicate", one.opts == same.opts)

print("\nthe language a job carries survives cleaning")
check("a plain code is kept",
      engine.clean_opts({"audio_lang": "en"}).get("audio_lang") == "en")
check("a star is not a language and is dropped",
      "audio_lang" not in engine.clean_opts({"audio_lang": "*"}),
      "rejected, as it should be")
check("something with a path in it is dropped",
      "audio_lang" not in engine.clean_opts({"audio_lang": "../../x"}))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
