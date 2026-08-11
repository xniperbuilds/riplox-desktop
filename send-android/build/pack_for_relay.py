"""
Put the built APK inside the relay, so the pairing page can hand it over.

The relay is already the one address a phone is sent to, so the app should be
downloadable from there rather than from a second place that has to be kept in
step. At around 100 KB of base64 it costs the Worker almost nothing.

Run after build.ps1, then deploy the relay.
"""
import base64
import hashlib
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(HERE, "AndroidManifest.xml")
# Worked out from this file's own location: the relay sits beside this app in
# the same repository, and a written-down path only ever works on one machine.
OUT = os.path.normpath(os.path.join(HERE, "..", "relay", "apk.js"))

ANDROID = "{http://schemas.android.com/apk/res/android}"

HEAD = """\
/**
 * Riplox Send, the phone app, base64 so the relay stays a single deploy.
 *
 * Why a native app exists at all: a web share target always opens the page it
 * belongs to - the platform offers no way not to - so sharing a video meant
 * watching a browser window appear and sit there. The app's share activity
 * finishes before it is drawn, so a share is a toast and nothing else.
 *
 * Built by SendToRiplox\\build.ps1, packed by build\\pack_for_relay.py.
 * Do not edit by hand.
 */

"""


def main() -> None:
    # The manifest decides the version, here and in build.ps1 both. The
    # in-app updater compares the code published below with the one it was
    # built with, so a version typed in twice is a version that eventually
    # disagrees with itself - and the update then either never shows up or
    # never goes away.
    root = ET.parse(MANIFEST).getroot()
    version = root.get(ANDROID + "versionName")
    code = int(root.get(ANDROID + "versionCode"))
    apk = os.path.join(HERE, "dist", "RiploxSend-v%s.apk" % version)
    if not os.path.exists(apk):
        raise SystemExit("no build for %s - run build.ps1 first (%s)" % (version, apk))

    with open(apk, "rb") as fh:
        raw = fh.read()

    digest = hashlib.sha256(raw).hexdigest().upper()
    encoded = base64.b64encode(raw).decode("ascii")

    with open(OUT, "w", encoding="ascii", newline="\n") as fh:
        fh.write(HEAD)
        fh.write('export const APK_VERSION = "%s";\n' % version)
        fh.write('export const APK_CODE = %d;\n' % code)
        fh.write('export const APK_SIZE = %d;\n' % len(raw))
        fh.write('export const APK_SHA256 = "%s";\n' % digest)
        fh.write('export const APK_B64 = "%s";\n' % encoded)

    print("apk    %s (code %d), %.1f KB" % (version, code, len(raw) / 1024.0))
    print("base64 %.1f KB" % (len(encoded) / 1024.0))
    print("sha256", digest)
    print("wrote ", OUT)


if __name__ == "__main__":
    main()
