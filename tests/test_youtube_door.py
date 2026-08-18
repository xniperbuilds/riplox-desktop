"""
The YouTube door, on its own - no engine, no queue, no yt-dlp.

Checks the link shapes it has to understand, the two ways it can answer
(merged pair / muxed single), and that a refusal comes back as a sentence
rather than a stack trace.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import doors                                                       # noqa: E402

PASS = FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


print("link shapes")
SHAPES = {
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?t=30&v=dQw4w9WgXcQ&list=x": "dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?si=abcdef": "dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ": "dQw4w9WgXcQ",
}
for link, want in SHAPES.items():
    try:
        got = doors._yt_id(link)
    except doors.DoorError as exc:
        got = f"refused: {exc}"
    check(link[:58], got == want, got)

print("\nlinks that are not one video")
for link in ("https://www.youtube.com/@channel",
             "https://www.youtube.com/playlist?list=PLabc",
             "https://www.youtube.com/results?search_query=cats"):
    try:
        doors._yt_id(link)
        check(link[:58], False, "accepted, should have been refused")
    except doors.DoorError as exc:
        check(link[:58], "will not guess" in str(exc), "refused with a reason")

print("\nrouting")
check("youtube.com routes to the youtube door",
      doors.site_of("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube")
check("youtu.be routes to the youtube door",
      doors.site_of("https://youtu.be/dQw4w9WgXcQ") == "youtube")
check("handles() says yes", doors.handles("https://youtu.be/dQw4w9WgXcQ"))

print("\naddress gate")
check("googlevideo is allowed",
      doors._address_ok("https://rr3---sn-x.googlevideo.com/videoplayback?x=1",
                        "youtube"))
check("a lookalike host is refused",
      not doors._address_ok("https://googlevideo.com.evil.net/x", "youtube"))
check("http is refused",
      not doors._address_ok("http://rr3.googlevideo.com/x", "youtube"))
check("a bare ip is refused",
      not doors._address_ok("https://127.0.0.1/x", "youtube"))
check("another site's cdn is refused",
      not doors._address_ok("https://cdninstagram.com/x", "youtube"))

print("\nlive: a public video, with a merger available")
try:
    got = doors.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        quality="1080", can_merge=True)
    check("answered", bool(got.get("url")), got.get("title", "")[:40])
    check("two streams to merge", bool(got.get("audio_url")),
          "video + audio" if got.get("audio_url") else "single stream")
    check("address is googlevideo", "googlevideo.com" in got["url"])
    check("carries the app's user agent",
          "youtube/" in got["headers"]["User-Agent"])
    check("has a title", bool(got.get("title")))
    check("has an uploader", bool(got.get("uploader")), got.get("uploader", ""))
    check("has a duration", (got.get("duration") or 0) > 0,
          f"{got.get('duration')}s")
    check("no note needed when the best was available", not got.get("note"),
          got.get("note", "") or "quiet")
except doors.DoorError as exc:
    check("answered", False, str(exc))

print("\nlive: the same video with no merger - muxed only")
try:
    got = doors.resolve("https://youtu.be/dQw4w9WgXcQ", quality="1080",
                        can_merge=False)
    check("answered", bool(got.get("url")))
    check("single stream", not got.get("audio_url"))
    check("says why it is not the best", bool(got.get("note")),
          got.get("note", "")[:70])
except doors.DoorError as exc:
    check("answered", False, str(exc))

print("\nlive: a height cap is respected")
try:
    low = doors.resolve("https://youtu.be/dQw4w9WgXcQ", quality="360",
                        can_merge=True)
    high = doors.resolve("https://youtu.be/dQw4w9WgXcQ", quality="best",
                         can_merge=True)
    check("360 asks for less than best", low["url"] != high["url"],
          "different streams")
except doors.DoorError as exc:
    check("height cap", False, str(exc))

print("\nlive: a video that does not exist")
try:
    doors.resolve("https://www.youtube.com/watch?v=aaaaaaaaaaa")
    check("refused with a sentence", False, "it answered for a fake id")
except doors.DoorError as exc:
    check("refused with a sentence", "YouTube" in str(exc), str(exc)[:70])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
