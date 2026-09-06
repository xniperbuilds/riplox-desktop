"""The engine update, and the digest it now has to match.

potoken.py will not run a byte it has not verified - a pinned release and two
SHA-256s, because it is a third-party binary. The download engine, which every
download runs through and which has updated itself by default since 3 Sep
2026, had no check at all: the zip was fetched over HTTPS and handed straight
to zipfile.extractall.

yt-dlp publishes SHA2-256SUMS beside the zip, so verifying it needs no pin -
only somewhere to read the number.

The rule, and it matters which way round it goes:

  * a MISMATCH fails closed. That is the case that means something.
  * a sums file that cannot be read fails OPEN, and says so. An engine that
    has fallen behind is the commonest reason a site stops working, and
    refusing every update over a missing side-file would trade a small risk
    for a certain one.

    python tests/test_engine_zip_is_checked.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-zipsum-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


print("-- where the digests are published " + "-" * 34)

for channel, url in engine._YTDLP_ZIP.items():
    sums = engine.sums_url_for(url)
    check("%s points at SHA2-256SUMS beside the zip" % channel,
          sums.endswith("/SHA2-256SUMS") and sums.rsplit("/", 1)[0]
          == url.rsplit("/", 1)[0], sums)

print("\n-- reading one digest out of the published file " + "-" * 21)

BODY = ("f" * 64 + "  yt-dlp\n"
        + "a" * 64 + "  yt-dlp_win.zip\n"
        + "b" * 64 + " *yt-dlp_macos\n")
check("the right line is picked", engine.digest_from_sums(BODY, "yt-dlp_win.zip")
      == "a" * 64)
check("a binary-mode star is not part of the name",
      engine.digest_from_sums(BODY, "yt-dlp_macos") == "b" * 64)
check("a name that is not there gives nothing",
      engine.digest_from_sums(BODY, "yt-dlp_linux") == "")
check("a name that only appears inside another is not matched",
      engine.digest_from_sums(BODY, "dlp_win.zip") == "")
check("rubbish in gives nothing out",
      engine.digest_from_sums("not a sums file at all", "yt-dlp_win.zip") == "")
check("an empty body gives nothing", engine.digest_from_sums("", "x") == "")

print("\n-- and the file on disk " + "-" * 45)

blob = SANDBOX / "engine.zip.part"
blob.write_bytes(b"riplox audit")
import hashlib
want = hashlib.sha256(b"riplox audit").hexdigest()
check("file_digest agrees with hashlib", engine.file_digest(blob) == want,
      engine.file_digest(blob)[:16])

print("\n-- the verdict, both ways round " + "-" * 37)

sums_body = {"text": want + "  yt-dlp_win.zip\n"}
real_urlopen = engine.urllib.request.OpenerDirector.open


class _Fake:
    def __init__(self, body):
        self.body = body

    def read(self, *a):
        return self.body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_open(self, request, timeout=None):
    if sums_body["text"] is None:
        raise OSError("404")
    return _Fake(sums_body["text"])


engine.urllib.request.OpenerDirector.open = fake_open
try:
    url = engine._YTDLP_ZIP["stable"]
    check("a matching digest is ok",
          engine._verify_engine_zip(blob, url) == "ok")

    sums_body["text"] = "c" * 64 + "  yt-dlp_win.zip\n"
    said = engine._verify_engine_zip(blob, url)
    check("a different digest is a mismatch", said.startswith("mismatch"), said[:56])

    sums_body["text"] = "d" * 64 + "  something_else.zip\n"
    said = engine._verify_engine_zip(blob, url)
    check("no digest for this name is unchecked, not a mismatch",
          said.startswith("unchecked"), said[:56])

    sums_body["text"] = None
    said = engine._verify_engine_zip(blob, url)
    check("a sums file that cannot be read is unchecked, not a mismatch",
          said.startswith("unchecked"), said[:56])
finally:
    engine.urllib.request.OpenerDirector.open = real_urlopen

print("\n-- and the caller only refuses on a mismatch " + "-" * 24)

source = (SRC / "engine.py").read_text(encoding="utf-8")
check("update_engine acts on 'mismatch'", 'startswith("mismatch")' in source)
check("it checks before unpacking",
      source.index("_verify_engine_zip(part") < source.index("ZipFile(part).extractall"))

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
