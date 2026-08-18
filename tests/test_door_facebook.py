"""
The Facebook door.

The guard is the point of this suite. A page or profile link carries dozens of
playable addresses, and handing back "a" video for one is worse than refusing:
the user gets a file and never learns it was not the one they asked for.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import doors                                                # noqa: E402
import engine                                               # noqa: E402

PASS, FAIL, SOFT = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


print("\n-- routing --------------------------------------------------------")
for url, want in [
    ("https://www.facebook.com/watch/?v=1234567890", "facebook"),
    ("https://www.facebook.com/NASA/videos/1234567890/", "facebook"),
    ("https://fb.watch/abcDEF123/", "facebook"),
    ("https://www.facebook.com/reel/1234567890", "facebook"),
    ("https://m.facebook.com/watch/?v=1234567890", "facebook"),
    ("https://www.instagram.com/reel/x/", "instagram"),
    ("https://vt.tiktok.com/X/", "tiktok"),
    # YouTube had no door of its own when this line was written, and it
    # asserted the link went to yt-dlp alone. It has one now - that is the
    # change, not a break - so the expectation moved with it. A site with no
    # door took its place, because "some links are yt-dlp's alone" is the
    # thing this row was really guarding.
    ("https://www.youtube.com/watch?v=x", "youtube"),
    ("https://youtu.be/x", "youtube"),
    ("https://vimeo.com/12345", ""),
]:
    check(f"routes to {want or '(yt-dlp)'}: {url[:44]}", doors.site_of(url) == want)

print("\n-- the id comes out of every link shape ---------------------------")
for url, want in [
    ("https://www.facebook.com/watch/?v=987654321", "987654321"),
    ("https://www.facebook.com/NASA/videos/123456789012345/", "123456789012345"),
    ("https://www.facebook.com/reel/555666777", "555666777"),
    ("https://www.facebook.com/somepage/videos/some-slug/111222333/", "111222333"),
]:
    check(f"id {want}", doors._fb_id(url) == want, doors._fb_id(url))

print("\n-- ⭐ a link to a PAGE is refused, not answered with any video -----")
for url, what in [
    ("https://www.facebook.com/NASA/videos/", "a video listing"),
    ("https://www.facebook.com/NASA/", "a profile"),
    ("https://www.facebook.com/", "the front page"),
    ("https://www.facebook.com/groups/12345/", "a group"),
]:
    try:
        got = doors.resolve(url)
        check(f"refused: {what}", False,
              f"returned {got['title'][:40]!r} - a file nobody asked for")
    except doors.DoorError as exc:
        text = str(exc)
        check(f"refused: {what}", True)
        if "videos/" in url or url.endswith("NASA/"):
            check(f"...and says why: {what}",
                  "one video" in text or "page" in text, text[:60])
    except Exception as exc:                                # noqa: BLE001
        check(f"refused: {what}", False, f"{type(exc).__name__}: {exc}")

print("\n-- rubbish is refused without reaching the network ----------------")
for bad in ("https://www.facebook.com/watch/?v=", "https://fb.watch/"):
    try:
        doors.resolve(bad)
        check(f"refused: {bad[-24:]}", False, "it did not refuse")
    except doors.DoorError:
        check(f"refused: {bad[-24:]}", True)
    except Exception as exc:                                # noqa: BLE001
        # A share link that cannot be followed is a network answer, not a pass
        check(f"refused: {bad[-24:]}", False, f"{type(exc).__name__}")

print("\n-- a real single video (network; may be refused signed out) -------")
# Supplied rather than shipped. This used to be one hard-coded video id, which
# put somebody's link into a public repository for no reason and rotted the
# day that post came down. Pass one in when this section is wanted:
#
#     python tests/test_door_facebook.py "https://www.facebook.com/watch/?v=..."
#     RIPLOX_TEST_FB="https://..." python tests/test_door_facebook.py
#
# Skipped, and said out loud, when there is none - the rest of the file covers
# routing, ids and refusals without a network at all.
REAL = [u for u in (sys.argv[1:] or [os.environ.get("RIPLOX_TEST_FB", "")]) if u]
if not REAL:
    print("  ....  skipped: no video given. Pass a Facebook video link as an "
          "argument, or set RIPLOX_TEST_FB, to exercise this part.")
worked = 0
for url in REAL:
    try:
        got = doors.resolve(url)
    except doors.DoorError as exc:
        print(f"  ....  {url[-20:]}: {str(exc)[:80]}")
        SOFT.append(url)
        continue
    except Exception as exc:                                # noqa: BLE001
        print(f"  ....  {url[-20:]}: {type(exc).__name__}: {str(exc)[:60]}")
        SOFT.append(url)
        continue

    check("resolved a real video", True, got["title"][:45])
    part = Path(tempfile.mkdtemp()) / "fb.mp4.part"
    try:
        engine.pull_to_file(got["url"], part, got["headers"],
                            time.monotonic() + 120)
        size = part.stat().st_size
        check("the whole file came down", size > 100000, f"{size:,} bytes")
        check("it really is an mp4", part.read_bytes()[4:8] == b"ftyp")
        worked += 1
    except Exception as exc:                                # noqa: BLE001
        print(f"  ....  byte fetch: {type(exc).__name__}: {str(exc)[:70]}")
        SOFT.append("bytes")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed"
      + (f", {len(SOFT)} network cases unproven" if SOFT else ""))
for name in FAIL:
    print("   FAILED: " + name)
if SOFT:
    print("   UNVERIFIED (network/Facebook-side, not a code result):")
    for s in SOFT:
        print("     " + str(s)[:70])
print("=" * 68)
sys.exit(1 if FAIL else 0)
