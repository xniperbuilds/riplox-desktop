"""The stable-named copies the website's buttons actually serve.

The site links to releases/latest/download/Riplox_Setup.exe, so that file - not
the versioned one - is what somebody downloads. Two things about it were wrong:

  * release_check.py asked whether it was present, resolved and downloaded.
    All three are true of a copy left over from the PREVIOUS release, which is
    how a 1.6.0 release nearly went out with two-day-old software under the
    stable names. Not a 404 - quietly wrong, which is worse.
  * SHA256SUMS.txt listed only the versioned names, so the site's promise that
    SHA-256 is published covered a file nobody downloads. sums.py's own
    docstring says why that matters: "a wrong hash is worse than a missing one:
    it asks to be verified and then fails the verification."

    python tests/test_release_stable_copies.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

import release_check                                        # noqa: E402
import sums                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


print("-- a stable copy left over from the last release " + "-" * 20)

FRESH = {"Riplox_Setup.exe": {"size": 75918104},
         "Riplox_Setup_v1.6.0.exe": {"size": 75918104},
         "Riplox_Portable.zip": {"size": 106831889},
         "Riplox_Portable_v1.6.0.zip": {"size": 106831889}}
STALE = dict(FRESH, **{"Riplox_Setup.exe": {"size": 75612345}})

check("a matching pair is not flagged",
      release_check.stale_reason("Riplox_Setup.exe", "v1.6.0", FRESH) == "")
why = release_check.stale_reason("Riplox_Setup.exe", "v1.6.0", STALE)
check("a copy of a different size IS flagged", bool(why), why[:70])
check("and the message names both files",
      "Riplox_Setup.exe" in why and "Riplox_Setup_v1.6.0.exe" in why)
check("the portable pair is judged the same way",
      release_check.stale_reason("Riplox_Portable.zip", "v1.6.0", FRESH) == "")

print("\n-- and when there is nothing to compare against " + "-" * 21)

check("no versioned twin means no verdict",
      release_check.stale_reason("Riplox_Setup.exe", "v1.6.0",
                                 {"Riplox_Setup.exe": {"size": 1}}) == "",
      "guessing would be worse than saying nothing")
check("a tag that matches nothing is not a failure",
      release_check.stale_reason("Riplox_Setup.exe", "v9.9.9", FRESH) == "")
check("the twin is worked out from the tag",
      release_check._versioned_twin("Riplox_Setup.exe", "v1.6.0", FRESH)
      == "Riplox_Setup_v1.6.0.exe")

print("\n-- what the checksums file covers " + "-" * 35)

published = [name for _, name, _ in sums.wanted()]
for name in ("Riplox_Setup.exe", "Riplox_Portable.zip"):
    check("SHA256SUMS covers %s" % name, name in published,
          "the name the site's button serves")
for name in ("Riplox_Setup_v", "Riplox_Portable_v"):
    check("and still covers the versioned %s.." % name,
          any(n.startswith(name) for n in published))
check("every published name is unique", len(published) == len(set(published)),
      published)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
