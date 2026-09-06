"""potoken.py - the module at the centre of the https failures, first tests.

Measured while auditing: of 74 test files, **none** imported this one. The
only mention of "potoken" anywhere in tests/ was a check that the string is a
key in DEFAULT_SETTINGS. So the 44 MB download, the pinned SHA-256 that
decides whether a third-party binary may run, the archive guard, the server
spawn and the empty-string promise had never been executed by a test - while
the module runs on every fresh install, without being asked.

Nothing here reaches the network or the real install: LOCALAPPDATA is
redirected before engine is imported, and the one function that fetches is
handed a fake response.

⚠️ kill_orphans() is deliberately NOT called. It runs
`taskkill /IM bgutil-pot.exe /F`, which would reach a helper belonging to the
real Riplox on this machine. A test that can kill the thing it is testing on
somebody's actual desktop is not a test worth having.

    python tests/test_the_helper_nobody_tested.py
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-helper-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import potoken                                              # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"
assert str(SANDBOX) in str(potoken.server_path()), "helper path is not sandboxed"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


class FakeResponse(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


def serve(body):
    """Point urlopen at these bytes, whatever is asked for."""
    potoken.urllib.request.urlopen = lambda *a, **k: FakeResponse(body)


print("-- the digest is the gate, not a formality " + "-" * 26)

good = b"a helper, pretend" * 100
right = hashlib.sha256(good).hexdigest()

serve(good)
check("a body that matches its digest comes back whole",
      potoken._download("https://x/y", right) == good)

serve(good + b"tampered")
try:
    potoken._download("https://x/y", right)
    check("a body that does NOT match is refused", False, "it was accepted")
except OSError as exc:
    check("a body that does NOT match is refused", True, str(exc)[:52])
    check("and the message says it was discarded", "discarded" in str(exc))

check("nothing was written while that happened",
      not potoken.server_path().exists(),
      "the digest is checked before the file exists, not after")

print("\n-- and the pinned digests are real " + "-" * 34)

for name, value in (("server", potoken.SERVER_SHA),
                    ("plugin", potoken.PLUGIN_SHA)):
    check("the %s digest is a full sha256" % name,
          len(value) == 64 and all(c in "0123456789abcdef" for c in value),
          value[:16] + "...")
check("they are not the same value", potoken.SERVER_SHA != potoken.PLUGIN_SHA)
check("the release is pinned too", potoken.RELEASE.startswith("v"),
      potoken.RELEASE)
check("and the download URL carries that release",
      potoken.RELEASE in potoken.BASE_URL, potoken.RELEASE)

print("\n-- an archive cannot write outside the folder it was aimed at " + "-" * 7)


def zip_with(names):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        for one in names:
            z.writestr(one, "x")
    raw.seek(0)
    return zipfile.ZipFile(raw)


target = SANDBOX / "extract-here"
target.mkdir(parents=True, exist_ok=True)

check("an ordinary archive is extracted",
      potoken._safe_extract(zip_with(["yt_dlp_plugins/a.py"]), target) is None)
check("and its file arrived", (target / "yt_dlp_plugins" / "a.py").exists())

for evil, what in ((["../escaped.py"], "one level up"),
                   (["yt_dlp_plugins/../../escaped.py"], "up through a subfolder"),
                   (["a/../../escaped.py"], "up the middle of a path")):
    try:
        potoken._safe_extract(zip_with(evil), target)
        check("refused: %s" % what, False, "it extracted %r" % evil[0])
    except OSError as exc:
        check("refused: %s" % what, True, str(exc)[:46])

check("and nothing escaped", not (SANDBOX / "escaped.py").exists(),
      "the guard runs before extractall, not after")

print("\n-- installed() needs BOTH halves " + "-" * 36)

potoken.remove()
check("a fresh sandbox has no helper", potoken.installed() is False)

engine.bin_dir().mkdir(parents=True, exist_ok=True)
potoken.server_path().write_bytes(b"not really an exe")
check("the exe on its own is NOT installed", potoken.installed() is False,
      "this is the state that makes the Settings toggle a lie")

(potoken.plugin_home() / "yt_dlp_plugins").mkdir(parents=True, exist_ok=True)
check("exe plus plugin folder IS installed", potoken.installed() is True)

potoken.server_path().unlink()
check("the plugin folder on its own is not either",
      potoken.installed() is False)

print("\n-- the plugin goes one level DOWN, or yt-dlp finds nothing " + "-" * 10)

check("plugin_home is inside plugin_dir",
      potoken.plugin_dir() in potoken.plugin_home().parents,
      "extracting straight into plugin_dir gives 'Plugin directories: none'")
check("and the package sits inside that",
      (potoken.plugin_home() / "yt_dlp_plugins").is_dir())
check("plugin_dir is what --plugin-dirs would be given",
      potoken.plugin_dir().name == "yt-dlp-plugins",
      potoken.plugin_dir().name)

print("\n-- the promise: a download still works when the helper does not " + "-" * 5)

potoken.remove()
check("not installed means no provider", potoken.ensure_running() == "",
      "an empty string, never an exception")
check("and it did not raise getting there", True)

print("\n-- remove() takes away both halves " + "-" * 34)

engine.bin_dir().mkdir(parents=True, exist_ok=True)
potoken.server_path().write_bytes(b"not really an exe")
(potoken.plugin_home() / "yt_dlp_plugins").mkdir(parents=True, exist_ok=True)
potoken.marker_file().write_text(json.dumps({"release": potoken.RELEASE}),
                                 encoding="utf-8")
check("installed before", potoken.installed() is True)
potoken.remove()
check("the exe is gone", not potoken.server_path().exists())
check("the plugin folder is gone", not potoken.plugin_dir().exists())
check("the marker is gone", not potoken.marker_file().exists())
check("and installed() agrees", potoken.installed() is False)

print("\n-- the small pieces " + "-" * 49)

port = potoken._free_port()
check("a free port is a real one", 1024 < port < 65536, port)
check("and nothing is listening on it", potoken._alive(port) is False,
      "asked before anything was started")

state = potoken.status()
check("status says it is not installed", state["installed"] is False, state)
check("status is not running", state["running"] is False)
check("status carries the size it will download",
      state["sizeMb"] == potoken.DOWNLOAD_MB, state["sizeMb"])
check("and the size is the one the window shows", potoken.DOWNLOAD_MB == 44,
      potoken.DOWNLOAD_MB)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
