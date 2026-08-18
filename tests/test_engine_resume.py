"""Does the engine download really resume, or does it just start again?

Serves a known 1 MB body from localhost. The first request is cut off after
300 KB on purpose - the same failure the real 18 MB download hit. The second
request must arrive with a Range header and come back 206, and the finished
file must match the original byte for byte.
"""
import hashlib
import http.server
import os
import sys
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

BODY = bytes((i * 7 + 3) % 256 for i in range(1024 * 1024))   # 1 MB, checkable
CUT_AT = 300 * 1024
seen = []          # every (range-header, status) the server answered


class Handler(http.server.BaseHTTPRequestHandler):
    cut_once = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        start = 0
        if rng and rng.startswith("bytes="):
            start = int(rng.split("=", 1)[1].split("-")[0])

        if start:
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{len(BODY)-1}/{len(BODY)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(BODY) - start))
        self.end_headers()
        seen.append((rng, 206 if start else 200))

        piece = BODY[start:]
        if Handler.cut_once:
            # Hang up mid-file exactly once - the dropped connection.
            Handler.cut_once = False
            self.wfile.write(piece[:CUT_AT])
            self.wfile.flush()
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return
        self.wfile.write(piece)


def main():
    import engine

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/yt-dlp_win.zip"

    part = Path(os.environ["TEMP"]) / "riplox_resume_test.part"
    part.unlink(missing_ok=True)
    deadline = time.monotonic() + 60

    # Attempt 1 - must fail, and must leave the part file behind.
    try:
        engine._download_engine_zip(url, part, deadline)
        print("FAIL: first attempt did not report the dropped connection")
        return 1
    except OSError as exc:
        print(f"attempt 1 failed as expected: {exc}")

    have = part.stat().st_size
    print(f"kept on disk after the drop: {have} bytes")
    if have != CUT_AT:
        print(f"FAIL: expected {CUT_AT} bytes kept, found {have}")
        return 1

    progress_mid = engine.engine_progress()

    # Attempt 2 - must resume rather than start again.
    engine._download_engine_zip(url, part, deadline)

    data = part.read_bytes()
    part.unlink(missing_ok=True)

    ok = True
    if data != BODY:
        print(f"FAIL: file differs - {len(data)} bytes, sha "
              f"{hashlib.sha256(data).hexdigest()[:16]}")
        ok = False
    if seen[1][0] != f"bytes={CUT_AT}-":
        print(f"FAIL: second request sent Range {seen[1][0]!r}")
        ok = False
    if seen[1][1] != 206:
        print("FAIL: second request was not a partial response")
        ok = False
    if not (0 < progress_mid["percent"] < 100):
        print(f"FAIL: percentage never moved: {progress_mid}")
        ok = False

    print(f"requests: {seen}")
    print(f"progress mid-download: {progress_mid['percent']}% "
          f"({progress_mid['message']})")
    print(f"final: {len(data)} bytes, matches original: {data == BODY}")
    print("ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
