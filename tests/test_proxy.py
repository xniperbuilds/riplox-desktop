"""
The proxy setting - including the part that is easy to get wrong.

Resolving a link through a proxy and then pulling the video around it would
look like a working feature and quietly hand the site the address the user
was hiding. So this does not stop at "the flag is in the command": it stands
up a real proxy on localhost, downloads through it, and checks the proxy's own
log to see that every request actually arrived there.
"""

import socket
import sys
import tempfile
import threading
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import doors                                                       # noqa: E402
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


# --------------------------------------------------------------------------
# A real proxy, so "it went through the proxy" is measured and not assumed
# --------------------------------------------------------------------------

class TinyProxy(threading.Thread):
    """
    The smallest thing that is genuinely an HTTPS proxy: it answers CONNECT by
    opening a socket to the named host and shovelling bytes both ways. It
    records every host it was asked for, and that record is the evidence.
    """

    daemon = True

    def __init__(self):
        super().__init__()
        self.seen = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(50)
        self.port = self.sock.getsockname()[1]
        self.stopped = False

    def run(self):
        while not self.stopped:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,),
                             daemon=True).start()

    def _serve(self, client):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                piece = client.recv(65536)
                if not piece:
                    return
                head += piece
            first = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target = first.split(" ")[:2]
            if method != "CONNECT":
                client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                return

            host, _, port = target.partition(":")
            self.seen.append(host)
            upstream = socket.create_connection((host, int(port or 443)), 20)
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

            def pump(source, sink):
                try:
                    while True:
                        data = source.recv(65536)
                        if not data:
                            break
                        sink.sendall(data)
                except OSError:
                    pass
                finally:
                    for side in (source, sink):
                        try:
                            side.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

            back = threading.Thread(target=pump, args=(upstream, client),
                                    daemon=True)
            back.start()
            pump(client, upstream)
            back.join(timeout=5)
        except Exception:                                        # noqa: BLE001
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    def close(self):
        self.stopped = True
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------

print("what counts as a proxy address")
GOOD = ["http://127.0.0.1:8080", "https://proxy.example:3128",
        "socks5://127.0.0.1:1080", "socks5h://host:1080",
        "http://user:pass@host:8080"]
for text in GOOD:
    check(text, engine.clean_proxy(text) == text and not engine.check_proxy(text))

print("\nand what does not")
BAD = {
    "127.0.0.1:8080": "needs to start with how to reach it",
    "ftp://host:21": "not a kind of proxy",
    "http://": "missing its host and port",
    "gopher://host": "not a kind of proxy",
}
for text, want in BAD.items():
    trouble = engine.check_proxy(text)
    check(text, want in trouble and engine.clean_proxy(text) == "",
          trouble[:60])

check("empty means direct, and is not an error",
      engine.clean_proxy("") == "" and engine.check_proxy("") == "")
check("whitespace is not a proxy",
      engine.clean_proxy("   ") == "" and engine.check_proxy("  ") == "")
check("the scheme is lowercased",
      engine.clean_proxy("HTTP://Host:8080") == "http://Host:8080")

print("\nthe engine passes it to yt-dlp")
try:
    args = engine._base_args({"proxy": "http://127.0.0.1:8080"})
    check("--proxy is in the command", "--proxy" in args,
          " ".join(args[args.index("--proxy"):args.index("--proxy") + 2])
          if "--proxy" in args else "")
    bare = engine._base_args({})
    check("and is absent when there is none", "--proxy" not in bare)
    junk = engine._base_args({"proxy": "127.0.0.1:8080"})
    check("a malformed one is dropped, not passed on", "--proxy" not in junk)
except engine.EngineMissing:
    print("  --    yt-dlp binary not present, skipping")

print("\nthe fallback route refuses to go around it")
doors.configure("socks5://127.0.0.1:1080")
check("SOCKS is refused rather than bypassed", bool(doors.proxy_problem()),
      doors.proxy_problem()[:60])
try:
    doors.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    check("resolve() stops before fetching anything", False, "it went ahead")
except doors.DoorError as exc:
    check("resolve() stops before fetching anything", "SOCKS" in str(exc) or
          "socks" in str(exc), str(exc)[:60])

doors.configure("http://127.0.0.1:8080")
check("an http proxy is accepted", not doors.proxy_problem())
doors.configure("")
check("no proxy is accepted", not doors.proxy_problem())

print("\nlive: everything really goes through the proxy")
proxy = TinyProxy()
proxy.start()
address = f"http://127.0.0.1:{proxy.port}"
work = Path(tempfile.mkdtemp(prefix="riplox_proxy_"))
try:
    doors.configure(address)
    got = doors.resolve("https://www.youtube.com/watch?v=jNQXAC9IVRw",
                        quality="360", can_merge=False)
    check("the link resolved through the proxy", bool(got.get("url")),
          got.get("title", "")[:36])
    check("the proxy saw youtube.com",
          any("youtube.com" in host for host in proxy.seen),
          ", ".join(sorted(set(proxy.seen))))

    before = len(proxy.seen)
    part = work / "clip.mp4.part"
    engine.pull_to_file(got["url"], part, got["headers"],
                        __import__("time").monotonic() + 120, proxy=address)
    check("the video bytes arrived", part.exists() and part.stat().st_size > 50_000,
          engine.human_bytes(part.stat().st_size) if part.exists() else "nothing")
    check("the bytes went through the proxy too", len(proxy.seen) > before,
          ", ".join(sorted(set(proxy.seen[before:]))) or "NOTHING - it went direct")
    check("and they went to googlevideo",
          any("googlevideo.com" in host for host in proxy.seen[before:]),
          ", ".join(sorted(set(proxy.seen[before:]))))
except doors.DoorError as exc:
    check("live run", False, str(exc)[:90])
finally:
    doors.configure("")
    proxy.close()
    shutil.rmtree(work, ignore_errors=True)



# --------------------------------------------------------------------------
# The race that a single shared value would have had
# --------------------------------------------------------------------------
# Downloads run several at a time, each in its own worker thread, and each one
# sets the proxy before it resolves. Held in a module-level variable, thread B
# overwrites thread A's a moment before A reads it - and the case that loses is
# a download meant to go through a proxy going out direct instead, which is the
# exact failure the whole feature exists to prevent.
#
# Written to fail against that version: with a shared value, at least one of
# these threads sees somebody else's proxy.

print("\ntwo downloads at once cannot take each other's proxy")

import threading                                                   # noqa: E402

seen = {}
start = threading.Barrier(8)


def pretend_download(n):
    mine = f"http://127.0.0.1:{9000 + n}"
    doors.configure(mine)
    start.wait(timeout=10)          # every thread configured; now they read
    for _ in range(200):
        if doors._proxy() != mine:
            seen[n] = doors._proxy()
            return
    seen[n] = mine


threads = [threading.Thread(target=pretend_download, args=(n,))
           for n in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=15)

wrong = {n: got for n, got in seen.items() if got != f"http://127.0.0.1:{9000 + n}"}
check("all eight kept their own", not wrong, wrong or "no crossover in 1,600 reads")
check("every thread actually ran", len(seen) == 8, f"{len(seen)}/8")

doors.configure("")
check("a thread that was never told has no proxy", doors._proxy() == "")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
