"""
Send to Riplox - a link shared from a phone, downloaded on this PC.

Two ways in, one pairing:

  * a relay - the PC dials out, so nothing on the internet can reach it. It
    holds the connection open and is handed anything waiting the moment it
    arrives. This is the path the phone's web page uses.
  * the LAN, for a paired app on the same WiFi - nothing leaves the building.

⚠ The web page cannot use the LAN, and that was measured rather than assumed:
a page served over HTTPS is forbidden by the browser from calling
http://192.168.x.x at all (mixed content), before any of our code runs. The
LAN listener is therefore for a native paired app, not the page. Claiming
"sent over your own WiFi" on that page would have been untrue.

Both paths carry the same sealed envelope: AES-GCM over {url, quality, trim}
with a nonce and a timestamp, so the relay holds ciphertext it cannot read and
a captured message cannot be replayed. A link is the only thing that travels -
video bytes never touch anyone else's server.

Deliberate limits, so the promises above stay true:
  * the LAN listener answers exactly two things - a ping, and a sealed
    envelope. It cannot open a file, read a setting or reach the app's own
    API, which is still bound to localhost behind a token.
  * pairing keys live here, never in settings.json, so a settings backup can
    never carry one machine's pairings onto another.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import cookies
import engine

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:                      # keeps the app usable without it
    AESGCM = None


LAN_PORT = 47811                 # fixed, so a paired phone can find it again
INVITE_LIFE = 120                # two minutes - a scan takes seconds
INVITE_MEMORY = 24 * 3600        # a dead invite's key is kept this long, and
                                 # only so its holder can be told which kind
                                 # of dead it is. It opens nothing.
REVOKED_MEMORY = 30 * 24 * 3600  # same idea for a removed device, kept longer
                                 # because a phone may not be picked up for
                                 # weeks and should still learn why it stopped

# A link sent while the PC was switched off is the whole point of the relay,
# so age alone cannot be what refuses a message. Replay is stopped by the
# nonce below, which is why this window can afford to be a week.
MESSAGE_LIFE = 7 * 24 * 3600
CLOCK_SLACK = 300                # a phone running fast, not a replay
SEEN_LIFE = MESSAGE_LIFE + 24 * 3600
MAX_SEEN = 4000

# The nonce above stops the same *message* twice. It cannot stop the same
# *link* twice, because a phone that re-sends seals a fresh envelope with a
# fresh nonce - two genuinely different messages that happen to carry the same
# URL. That is not hypothetical: fourteen links shared while the PC was off
# came back with four copies of some of them.
#
# So the same link, from the same device, arriving again while the first copy
# is still in hand is treated as the same link arriving again - not as a
# request to download it twice. It is written into the log as "duplicate" so
# it is visible rather than silently dropped, and the window is short enough
# that deliberately sending something again later still works.
DUPLICATE_WINDOW = 15 * 60
MAX_RECENT = 200

MAX_BODY = 4096                  # an envelope is a few hundred bytes
POLL_SECONDS = 25                # how long the relay holds our request open

# How long the socket may sit quiet before saying hello again. It is not a
# keep-alive for the connection - Cloudflare's own pings do that - it is a
# check that the line is still real, because a socket that has silently died
# looks exactly like a socket with nothing to say.
SOCKET_IDLE = 240

# What a per-device rule is allowed to name. Both lists come from the engine,
# so a quality or a site added there cannot quietly become one no rule can
# mention - and nothing a phone sends is ever passed through unchecked.
QUALITIES = tuple(engine.QUALITY_LABELS)
SITES = engine.known_sites()

# Highest first. A cap is "no better than this", so the answer to a request
# above the cap is the cap, not a refusal.
_QUALITY_ORDER = ("best", "2160", "1440", "1080", "720", "480", "360")

# Measured, not guessed: urllib's default "Python-urllib/3.11" is refused by
# Cloudflare's bot rules with a 403, so the relay would never have connected
# once. Say who we are.
RELAY_HEADERS = {
    "User-Agent": "Riplox/1.0.0 (+https://xniperbuilds.com)",
    "Accept": "application/json",
}

_lock = threading.RLock()
_sink = None                     # set by app.py; queues the download
_started = False
_lan_server = None
_stop = threading.Event()
_status = {"relay": "off", "lan": "off", "error": ""}


# --------------------------------------------------------------------------
# Stored state
# --------------------------------------------------------------------------

def _file() -> Path:
    return engine.data_dir() / "share.json"


def _blank() -> dict:
    return {"room": secrets.token_hex(16), "devices": [], "invite": None,
            "pending": [], "log": [], "spent": [], "revoked": [], "seen": {},
            "recent": {}}


def load() -> dict:
    with _lock:
        try:
            with open(_file(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = _blank()
            _save(data)
            return data

        for key, value in _blank().items():
            data.setdefault(key, value)
        return data


def _save(data: dict) -> None:
    path = _file()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def save(data: dict) -> None:
    with _lock:
        _save(data)


# --------------------------------------------------------------------------
# The sealed envelope
# --------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _open_envelope(key_b64: str, nonce_b64: str, cipher_b64: str):
    """Decrypt one message, or return None if it was not ours."""
    if AESGCM is None:
        return None
    try:
        nonce = _b64d(nonce_b64)
        if len(nonce) != 12:
            return None
        raw = AESGCM(_b64d(key_b64)).decrypt(nonce, _b64d(cipher_b64), None)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        # A message meant for a different device, or tampered with. Both are
        # ordinary and neither is worth a log line an attacker could fill.
        return None


def _seen(nonce_b64: str) -> bool:
    """Has this message already been dealt with?

    On disk, not in memory. The point of the relay holding a message while the
    PC is off is that it arrives after a restart - so a replay guard that
    forgets on restart would not be a guard at all.
    """
    with _lock:
        return nonce_b64 in (load().get("seen") or {})


def _burn(nonce_b64: str, now: float) -> None:
    """
    Record that this message has been dealt with, so a redelivery is ignored.

    Split from the check on purpose, and called only once the message has
    actually landed somewhere. Recording it up front - which is what this used
    to do - meant that a link refused for any reason at all was already marked
    as handled: the relay would redeliver it, this end would call it a replay,
    and it was gone for good with nothing written anywhere. Links sent while
    the PC was off went missing exactly this way.
    """
    with _lock:
        data = load()
        seen = data.get("seen") or {}
        if nonce_b64 in seen:
            return

        seen[nonce_b64] = now
        if len(seen) > MAX_SEEN:
            seen = {n: t for n, t in seen.items() if t > now - SEEN_LIFE}
            if len(seen) > MAX_SEEN:                       # still too many
                newest = sorted(seen.items(), key=lambda kv: kv[1])[-MAX_SEEN:]
                seen = dict(newest)
            seen[nonce_b64] = now

        data["seen"] = seen
        _save(data)


def _fresh(body: dict, nonce_b64: str) -> str:
    """"ok", or why this message is being refused."""
    try:
        sent = float(body.get("ts") or 0)
    except (TypeError, ValueError):
        return "stale"

    now = time.time()
    if sent < now - MESSAGE_LIFE or sent > now + CLOCK_SLACK:
        return "stale"
    if _seen(nonce_b64):
        return "replay"
    return "ok"


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------

def relay_base() -> str:
    """The relay as an https origin, whatever it was typed as."""
    url = (engine.load_settings().get("share_relay")
           or engine.DEFAULT_RELAY).strip().rstrip("/")
    if url.startswith("wss://"):
        return "https://" + url[6:]
    if url.startswith("ws://"):
        return "http://" + url[5:]
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def lan_ip() -> str:
    """This machine's address on its own network, without asking anyone."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))   # no packet is actually sent
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def _looks_like_key(text: str) -> bool:
    try:
        return len(_b64d(text)) == 32
    except Exception:
        return False


def _spend(spent, invite: dict, why: str) -> list:
    """
    Keep a dead invite's key just long enough to answer honestly.

    Without this a phone holding a used or expired link gets "unknown", which
    the page can only report as "could not reach your PC" - the one thing that
    is not true. The key here unlocks nothing: it is only ever compared, and
    anything it opens is refused with the reason it is being kept for.
    """
    now = time.time()
    out = [s for s in (spent or []) if s.get("at", 0) > now - INVITE_MEMORY]
    out.insert(0, {"key": invite["key"], "why": why, "at": now})
    return out[:20]


def make_invite(name: str = "") -> dict:
    """A one-time pairing code, good for two minutes and one device."""
    data = load()
    if data.get("invite"):
        # Showing a new code retires the old one. Its holder is told that,
        # rather than being left guessing.
        data["spent"] = _spend(data.get("spent"), data["invite"], "expired")
    key = _b64e(secrets.token_bytes(32))
    invite = {
        "code": secrets.token_urlsafe(9),
        "key": key,
        # Kept exactly as typed, empty included. A name the host chose has to
        # outrank the one the phone guesses about itself - "Ali" is the whole
        # point of typing it, and "Android phone" is what arrives otherwise.
        "name": (name or "").strip()[:24],
        "expires": time.time() + INVITE_LIFE,
    }
    data["invite"] = invite
    save(data)

    ip = lan_ip()
    # The key rides in the fragment, which browsers never send to a server -
    # so the relay is handed the room and nothing else.
    url = (f"{relay_base()}/p/{data['room']}"
           f"#k={key}&c={invite['code']}"
           + (f"&l={ip}:{LAN_PORT}" if ip else ""))

    import segno
    qr = segno.make(url, error="m")
    return {
        "url": url,
        # The same three values as the link, in a form that can be typed or
        # pasted into the phone's page. A camera is not always an option - a
        # cracked lens, a locked-down work phone, a QR that will not focus -
        # and it is read in the browser, so the key still never reaches the
        # relay.
        "code": f"{data['room']}.{key}.{invite['code']}",
        "svg": qr.svg_inline(scale=4, dark="#000000", light="#ffffff",
                             border=1),
        "expires": invite["expires"],
        "name": invite["name"],
    }


def _claim(body: dict) -> bool:
    """A phone saying hello with a live invite becomes a paired device."""
    data = load()
    invite = data.get("invite")
    if not invite or invite.get("expires", 0) < time.time():
        return False
    if body.get("code") != invite.get("code"):
        return False

    # The phone brings its own key, sealed inside a message only the holder of
    # the invite could have written. The key printed in the pairing link is
    # therefore spent the moment it is used - anyone who later finds that link,
    # in a chat or a screenshot or a browser history, holds something that no
    # longer opens anything.
    #
    # Without this the link *is* the device, permanently, and the screen's
    # promise of "one device" would simply not be true.
    own = str(body.get("key") or "")
    key = own if _looks_like_key(own) else invite["key"]

    device = {
        "id": secrets.token_hex(6),
        "name": str(invite.get("name") or body.get("name") or "Phone")[:24],
        "key": key,
        "added": time.time(),
        "last": time.time(),
        "count": 0,
        "paused": False,
    }
    data["devices"].append(device)
    data["invite"] = None            # single use, as promised on the screen
    if key != invite["key"]:
        data["spent"] = _spend(data.get("spent"), invite, "used")
    save(data)
    return True


def _retire(data: dict, devices: list) -> None:
    """
    Remember a removed device's key long enough to tell it it was removed.

    Without this a revoked phone and a switched-off PC look identical from the
    phone: both are silence, because a key we no longer hold cannot open the
    message and an unopenable message is never answered. The key is kept only
    to be recognised - anything it opens is refused with "revoked".
    """
    now = time.time()
    kept = [r for r in data.get("revoked", [])
            if r.get("at", 0) > now - REVOKED_MEMORY]
    for device in devices:
        kept.insert(0, {"key": device["key"], "name": device.get("name", ""),
                        "why": "revoked", "at": now})
    data["revoked"] = kept[:40]


def revoke(device_id: str) -> bool:
    data = load()
    going = [d for d in data["devices"] if d["id"] == device_id]
    if not going:
        return False
    data["devices"] = [d for d in data["devices"] if d["id"] != device_id]
    _retire(data, going)
    save(data)
    return True


def revoke_all() -> int:
    """
    Cut every device off at once.

    Worth its own button rather than a row of Removes: the moment you want
    this is the moment something has gone wrong, and that is not when someone
    should be clicking carefully through a list.
    """
    data = load()
    going = list(data["devices"])
    data["devices"] = []
    data["invite"] = None
    _retire(data, going)
    save(data)
    return len(going)


def set_paused(device_id: str, paused: bool) -> bool:
    """
    Stop taking links from one device without cutting it off.

    Revoking is the only honest answer to a device you no longer trust, but it
    is a one-way door: the phone has to be paired again from scratch. Most of
    the time what is wanted is "not right now", so that is its own switch.
    """
    data = load()
    hit = False
    for device in data["devices"]:
        if device["id"] == device_id:
            device["paused"] = bool(paused)
            hit = True
    if hit:
        save(data)
    return hit


def rename(device_id: str, name: str) -> bool:
    data = load()
    clean = "".join(c for c in str(name or "") if c.isalnum() or c in " -_")
    clean = clean.strip()[:24]
    if not clean:
        return False
    for device in data["devices"]:
        if device["id"] == device_id:
            device["name"] = clean
            save(data)
            return True
    return False


def set_limits(device_id: str, limits: dict) -> bool:
    """Per-device rules. Anything left out is simply not enforced."""
    clean = {}

    for key in ("per_day", "max_mb", "total_gb"):
        try:
            value = int(limits.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        clean[key] = max(0, min(value, 100000))       # 0 means no limit

    cap = str(limits.get("quality_cap") or "")
    clean["quality_cap"] = cap if cap in QUALITIES else ""

    sites = limits.get("sites")
    if isinstance(sites, list):
        clean["sites"] = [s for s in (str(x)[:20] for x in sites) if s in SITES]
    else:
        clean["sites"] = []                           # empty means anywhere

    clean["own_folder"] = bool(limits.get("own_folder"))
    clean["approve"] = bool(limits.get("approve"))

    data = load()
    for device in data["devices"]:
        if device["id"] == device_id:
            device["limits"] = clean
            save(data)
            return True
    return False


# --------------------------------------------------------------------------
# Handling a message
# --------------------------------------------------------------------------

def set_sink(fn) -> None:
    """app.py hands us the thing that queues a download."""
    global _sink
    _sink = fn


def _note(entry: dict) -> None:
    data = load()
    data["log"] = _trimmed([entry] + data.get("log", []))
    save(data)


LOG_KEEP = 40


def _trimmed(log: list) -> list:
    """
    The last forty entries - plus every link still waiting, however old.

    A held link is not history, it is something still to be decided. Trimming
    the list by age alone meant a busy day could push one off the end and take
    it with it, which is the same silent loss the holding was there to prevent.
    """
    waiting = [e for e in log if e.get("state") == "waiting"]
    settled = [e for e in log if e.get("state") != "waiting"][:LOG_KEEP]
    keep = {id(e) for e in waiting} | {id(e) for e in settled}
    return [e for e in log if id(e) in keep]


def handle(nonce_b64: str, cipher_b64: str, via: str = "lan") -> str:
    """
    Try every paired device's key, then a live invite, then the dead ones.

    Returns "unknown" when nothing could open it - a stranger on the network
    learns only that, never whether their guess was close. Anything else is
    only ever told to something that already holds the key, which is why it
    can afford to be specific: a phone whose clock has drifted needs to be
    told that, not shown "Sent" over a message that was thrown away.
    """
    data = load()
    candidates = [(d, d["key"], "") for d in data.get("devices", [])]

    invite = data.get("invite")
    if invite:
        live = invite.get("expires", 0) > time.time()
        candidates.append((None, invite["key"], "" if live else "expired"))

    # Dead keys are tried last and can only ever produce a reason. Without
    # them a used or expired link gets "unknown", which the phone can only
    # report as "could not reach your PC" - the one answer that is not true.
    for dead in data.get("spent", []):
        candidates.append((None, dead["key"], dead.get("why", "expired")))
    for dead in data.get("revoked", []):
        candidates.append((None, dead["key"], "revoked"))

    for device, key, dead in candidates:
        body = _open_envelope(key, nonce_b64, cipher_b64)
        if body is None:
            continue

        verdict = dead or _fresh(body, nonce_b64)
        if verdict == "ok":
            # Only now, with the message decrypted and about to be acted on.
            # Whatever the outcome below, it lands somewhere this end can show:
            # queued, held for approval, or held with the reason it could not
            # start. Nothing is refused into silence any more, which is what
            # makes it safe to mark this one as handled.
            _burn(nonce_b64, time.time())

            kind = body.get("kind")
            if kind == "hello":
                verdict = ("paired" if (device is not None or _claim(body))
                           else "expired")
            elif kind == "ping":
                # "Is my PC on, and does it still know me?" - answered without
                # touching the queue or the activity log, so a phone can ask
                # as often as it likes.
                verdict = "paused" if (device or {}).get("paused") else "pong"
            else:
                verdict = _accept(device, body)

        # The LAN answers in the same breath. Over the relay there is nothing
        # to answer into, so the verdict is posted back separately.
        if via == "relay":
            _reply(key, body.get("r"), verdict)
        return verdict

    return "unknown"


def _capped(quality: str, cap: str) -> str:
    """A cap means "no better than this", so it lowers a request, never refuses it."""
    if not cap or cap not in _QUALITY_ORDER:
        return quality
    if quality == "mp3":                       # audio is under every ceiling
        return quality
    if quality not in _QUALITY_ORDER:
        return cap
    return quality if _QUALITY_ORDER.index(quality) >= _QUALITY_ORDER.index(cap) else cap


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _written_by_device() -> dict:
    """
    Bytes each device has actually put on this disk.

    Measured from files that landed, not from links that were asked for: a
    download that failed has cost the disk nothing and should not count
    against an allowance.
    """
    totals = {}
    for item in engine.load_history():
        who = item.get("from")
        if not who:
            continue
        try:
            totals[who] = totals.get(who, 0) + int(item.get("bytes") or 0)
        except (TypeError, ValueError):
            pass
    return totals


def _usage(device, written=None) -> tuple:
    """
    (links accepted today, bytes written) for one device.

    Today's count is kept on the device itself rather than counted out of the
    activity log. The log holds the last forty entries, so a limit of fifty
    could never have been reached by counting it - the number simply stopped
    going up.
    """
    device = device or {}
    if not device.get("id"):
        return 0, 0

    today = (int(device.get("day_count") or 0)
             if device.get("day") == _today() else 0)
    if written is None:
        written = _written_by_device()
    return today, written.get(device["id"], 0)


def _device_dir(name: str):
    """`From <name>` under the download folder, made only when it is wanted."""
    clean = "".join(c for c in str(name or "") if c.isalnum() or c in " -_").strip()
    root = Path(engine.load_settings()["download_dir"]) / f"From {clean or 'phone'}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return str(root)
    except OSError:
        return ""          # fall back to the usual folder rather than fail


def _blocked_by(device, limits: dict, url: str) -> str:
    """
    Which of this device's rules stops this link, or "".

    Worked out in one place and returned rather than acted on, because none of
    these are reasons to throw a link away. A daily limit is over by tomorrow,
    a pause is undone by a click, a site list can be edited. The link is kept
    either way and the reason travels with it.
    """
    if (device or {}).get("paused"):
        return "paused"

    allowed = limits.get("sites") or []
    if allowed and engine.site_of(url) not in allowed:
        return "site"

    today, written = _usage(device)
    per_day = int(limits.get("per_day") or 0)
    if per_day and today >= per_day:
        return "day-limit"

    total_gb = int(limits.get("total_gb") or 0)
    if total_gb and written >= total_gb * (1024 ** 3):
        return "total-limit"

    return ""


def _link_key(url: str, device_id: str) -> str:
    """One short name for "this link, from this phone"."""
    raw = f"{device_id or ''}\n{url}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:16]


def _remember_link(url: str, device_id: str) -> None:
    """
    Note that this link is in hand, so a copy of it can be recognised.

    Kept in its own small record rather than read back out of the activity
    log, and that is not tidiness. The log holds the last forty settled
    entries: fifty other links arriving in between would push this one off the
    end, and a copy after that would no longer look like a copy - the same
    duplicate bug, just delayed by a busy day. Measured, not imagined; the
    log-reading version of this check failed exactly that test.

    Trimmed by age first, so the usual case never grows, and capped after that
    so nothing can make it grow anyway.
    """
    now = time.time()
    with _lock:
        data = load()
        recent = {k: t for k, t in (data.get("recent") or {}).items()
                  if isinstance(t, (int, float)) and now - t <= DUPLICATE_WINDOW}
        recent[_link_key(url, device_id)] = now
        if len(recent) > MAX_RECENT:
            newest = sorted(recent.items(), key=lambda kv: kv[1])[-MAX_RECENT:]
            recent = dict(newest)
        data["recent"] = recent
        _save(data)


def _forget_link(url: str, device_id: str) -> None:
    """
    Drop a link from that record, so sending it again works normally.

    Used when someone says No to one. Refusing is a decision about that link,
    and a link sent again after that is a new decision to make - not a copy of
    one already in hand.
    """
    with _lock:
        data = load()
        recent = data.get("recent") or {}
        if recent.pop(_link_key(url, device_id), None) is not None:
            data["recent"] = recent
            _save(data)


def _already_here(url: str, device_id: str) -> bool:
    """Is this exact link, from this exact device, already in hand?"""
    when = (load().get("recent") or {}).get(_link_key(url, device_id))
    try:
        return time.time() - float(when) <= DUPLICATE_WINDOW
    except (TypeError, ValueError):
        return False


# Text, not a link
# --------------------------------------------------------------------------
# What people actually send this way is a licence key, a password, a Wi-Fi
# code - something awkward to retype and worth protecting. That shapes every
# decision below far more than the feature name does.

# Bytes, not characters, and measured rather than picked: the relay accepts a
# ciphertext field of at most 4000 base64 characters, which is 3000 bytes,
# less 16 for the AES-GCM tag and a little for the JSON around it. 2,900 bytes
# leaves a margin and still carries an SSH private key (about 1,700). Counting
# characters would have been wrong for anyone not typing English - a 22
# character Urdu sentence is 39 bytes.
TEXT_MAX = 2900

# Long enough to still be there when somebody gets to their desk, short enough
# that a password is not left lying about. Taking it removes it either way.
TEXT_TTL = 24 * 3600


def _take_text(text: str, device) -> str:
    """
    Keep a piece of sent text until its owner comes to collect it.

    Three things this deliberately does not do, each for a reason:

    It is never truncated. Half a licence key that looks whole is worse than
    a refusal - it would be pasted, and it would fail somewhere confusing. Too
    long is answered as too long.

    It is never written to disk in the clear. It goes through the same DPAPI
    sealing the saved sign-ins use, so share.json holds ciphertext that only
    this Windows account on this machine can open.

    It never reaches the clipboard on its own. Writing another machine's
    clipboard unprompted is how a paste turns into something the user did not
    choose; collecting it is a deliberate press, and it disappears afterwards.
    """
    raw = text.encode("utf-8")
    if len(raw) > TEXT_MAX:
        return "text-too-long"

    sealed = cookies.seal(text)
    if not sealed:
        # Sealing failing means it cannot be stored safely, and storing it
        # unsafely is not the fallback. Better to refuse and say so.
        return "text-unsealable"

    settings = engine.load_settings()
    limits = (device or {}).get("limits") or {}
    hold = bool(settings.get("share_approve") or limits.get("approve"))

    _note({
        "id": secrets.token_hex(6),
        "kind": "text",
        "sealed": sealed,
        # For the screen: enough to recognise it by, never the thing itself.
        "chars": len(text),
        "from": (device or {}).get("name", "a paired device"),
        "device": (device or {}).get("id", ""),
        "at": time.time(),
        "expires": time.time() + TEXT_TTL,
        "state": "waiting" if hold else "ready",
    })
    return "held" if hold else "queued"


def _accept(device, body: dict) -> str:
    """
    Apply this device's rules, then queue it. Returns what to tell the phone.

    A link that a rule stops is *held*, not dropped. It goes into the activity
    log in the same "waiting" state an approval uses, so it appears with the
    same Approve button and the same badge, and the reason is written beside
    it. Before this, a link refused by a limit was answered to the phone and
    then existed nowhere: the message had already been marked as handled, so
    the relay's redelivery was refused as a replay and it was gone. Sending
    several at once with the PC off was the reliable way to lose some.
    """
    url = str(body.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")) or len(url) > 2000:
        # No usable link. It may still be a piece of text somebody meant to
        # send - a licence key, a password, a line to paste. A link always
        # wins where there is one: that is what this app is for, and an
        # existing share must not change shape because of this branch.
        text = str(body.get("text") or "")
        if text.strip():
            return _take_text(text, device)
        return "bad-link"          # never going to work; nothing to keep

    settings = engine.load_settings()
    limits = (device or {}).get("limits") or {}
    device_id = (device or {}).get("id", "")

    blocked = _blocked_by(device, limits, url)

    quality = str(body.get("quality") or settings.get("default_quality", "best"))
    if quality not in QUALITIES:
        quality = settings.get("default_quality", "best")
    quality = _capped(quality, limits.get("quality_cap") or "")

    opts = {}
    if limits.get("own_folder") and device is not None:
        folder = _device_dir(device.get("name"))
        if folder:
            opts["dest_dir"] = folder
    if limits.get("max_mb"):
        # Handed to the engine rather than checked here: yt-dlp knows the size
        # before it writes anything, so nothing half-downloaded is left behind.
        opts["max_mb"] = int(limits["max_mb"])

    hold = bool(settings.get("share_approve") or limits.get("approve"))
    who = (device or {}).get("name", "a paired device")
    entry = {
        "id": secrets.token_hex(6),
        "url": url,
        "quality": quality,
        "from": who,
        "device": device_id,
        "opts": opts,
        "at": time.time(),
        # "waiting" either way, so a held link gets the Approve button and the
        # badge the screen already draws for one. What differs is why.
        "state": "waiting" if (hold or blocked) else "queued",
    }
    if blocked:
        entry["why"] = blocked

    # The same link twice from the same phone is one link, sent twice. It is
    # logged rather than dropped - "duplicate" beside the original is the
    # difference between a guard anyone can see working and a link that went
    # missing - and it starts nothing, counts for nothing, and is not a second
    # thing to approve.
    if _already_here(url, device_id):
        entry["state"] = "duplicate"
        entry.pop("why", None)
        _note(entry)
        # A link that is still arriving is still the same link arriving, so
        # the window runs from the last time it turned up rather than the
        # first: a sender that keeps trying cannot simply out-wait this.
        _remember_link(url, device_id)
        return "duplicate"

    if device is not None:
        data = load()
        for d in data["devices"]:
            # A link that never started has cost nothing, so it is not counted
            # against an allowance - otherwise a device at its daily limit
            # would push its own held links further out of reach every time.
            if d["id"] == device["id"] and not blocked:
                d["last"] = time.time()
                d["count"] = int(d.get("count", 0)) + 1
                # Today's tally lives here so a daily limit larger than the
                # activity log can still be reached.
                if d.get("day") != _today():
                    d["day"] = _today()
                    d["day_count"] = 0
                d["day_count"] = int(d.get("day_count") or 0) + 1
        data["log"] = _trimmed([entry] + data.get("log", []))
        save(data)
    else:
        _note(entry)

    # In hand now, whether it is downloading or waiting to be approved. Either
    # way a second copy of it is a copy, not a second thing to do.
    _remember_link(url, device_id)

    if entry["state"] == "queued" and _sink:
        _sink(url, quality, who, opts, device_id)

    # The phone is told the rule that stopped it, not a bare "held" - "your
    # daily limit is up" and "your PC is asking first" are different news.
    if blocked:
        return blocked
    return "queued" if not hold else "held"


def approve(entry_id: str, ok: bool) -> bool:
    """
    Start, or throw away, a link that was held.

    Held for approval or held by a rule - the same button either way. Saying
    yes to one a rule stopped is a decision about that link, so the rule is not
    consulted again: the person looking at the screen outranks the allowance
    they set themselves.
    """
    data = load()
    for entry in data.get("log", []):
        if entry.get("id") == entry_id and entry.get("state") == "waiting":
            entry["state"] = "queued" if ok else "refused"
            entry.pop("why", None)
            save(data)
            if not ok:
                # Said no to it, so it is no longer in hand: sending it again
                # is a fresh decision rather than a copy of this one.
                _forget_link(entry.get("url", ""), entry.get("device", ""))
            if ok and _sink:
                _sink(entry["url"], entry["quality"], entry.get("from", ""),
                      entry.get("opts") or {}, entry.get("device", ""))
            return True
    return False


# --------------------------------------------------------------------------
# Answering the phone
# --------------------------------------------------------------------------

_RID_RE = re.compile(r"[A-Za-z0-9_-]{6,40}")


def _seal(key_b64: str, body: dict):
    """The same envelope the phone sends, in the other direction."""
    if AESGCM is None:
        return None
    try:
        nonce = secrets.token_bytes(12)
        raw = json.dumps(body).encode("utf-8")
        cipher = AESGCM(_b64d(key_b64)).encrypt(nonce, raw, None)
        return {"n": _b64e(nonce), "c": _b64e(cipher)}
    except Exception:
        return None


def _reply(key_b64: str, rid, verdict: str) -> None:
    """
    Tell the phone what actually became of its message.

    The relay is a postbox: it can honestly say "left for your PC" and nothing
    more, because it cannot read what it carries. Without this the page could
    only ever show "Sent" - including for a link refused as a replay, sent from
    a paused device, or sent with a pairing code that had already been used.

    The verdict is sealed with the same key the message arrived under, so only
    its sender can read it. A stranger, whose message could not be opened at
    all, is never replied to - there is no key to reply with, and nothing they
    should learn.
    """
    rid = str(rid or "")
    if not _RID_RE.fullmatch(rid):
        return

    envelope = _seal(key_b64, {"why": verdict, "ts": time.time()})
    if not envelope:
        return
    envelope["r"] = rid

    body = json.dumps(envelope).encode("utf-8")
    request = urllib.request.Request(
        f"{relay_base()}/ack/{load()['room']}", data=body, method="POST",
        headers=dict(RELAY_HEADERS, **{"Content-Type": "application/json"}))
    try:
        urllib.request.urlopen(request, timeout=8).close()
    except Exception:
        pass          # the phone falls back to "left for your PC", as before


# --------------------------------------------------------------------------
# The LAN listener
# --------------------------------------------------------------------------

class _LanHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Riplox"
    sys_version = ""

    def log_message(self, *args) -> None:      # no console spew
        pass

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self._json(200, {"ok": True})

    def do_GET(self) -> None:
        # A phone confirming it has found the right PC before it sends. The
        # room is not a secret on its own; the key is, and that is not here.
        if self.path == "/lan-ping":
            self._json(200, {"ok": True, "room": load()["room"]})
        else:
            self._json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/lan-send":
            self._json(404, {"ok": False})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"ok": False})
            return

        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            verdict = handle(str(body.get("n", "")), str(body.get("c", "")))
        except Exception:
            verdict = "unknown"

        # A stranger gets a flat no and learns nothing. A device that holds
        # the key gets the truth, because "Sent" over a message that was
        # dropped is the worst answer of the three.
        if verdict == "unknown":
            self._json(200, {"ok": False})
        else:
            self._json(200, {"ok": verdict in ("queued", "paired"), "why": verdict})


def _serve_lan() -> None:
    global _lan_server
    try:
        _lan_server = ThreadingHTTPServer(("0.0.0.0", LAN_PORT), _LanHandler)
    except OSError as exc:
        _status["lan"] = "unavailable"
        _status["error"] = f"LAN port {LAN_PORT} is in use ({exc.errno})."
        return

    _lan_server.daemon_threads = True
    _status["lan"] = "listening"
    try:
        _lan_server.serve_forever(poll_interval=0.5)
    except Exception:
        pass
    finally:
        _status["lan"] = "off"


# --------------------------------------------------------------------------
# The relay
# --------------------------------------------------------------------------

# When one message may be let go: after this many failures AND not before this
# much time has passed since the first of them.
#
# The count alone was not enough, and the reason is worth writing down. A relay
# holding a message answers the poll immediately, and the loop re-opens the
# poll the moment it returns - so three failures happen back to back. Measured:
# a link that failed three times and would have worked on the fourth was given
# up on in 31 milliseconds. Every transient worth surviving - a file still
# locked by the last download, a folder being written to, a disk briefly full -
# lasts longer than that, so the rule protected against nothing.
#
# Two minutes is long enough for those to clear and short enough that a link
# which can never be handled is not still circling when anyone notices.
GIVE_UP_AFTER = 3
GIVE_UP_NOT_BEFORE = 120

# nonce -> [failures, when the first one happened]. In memory only: a restart
# is exactly the kind of thing that fixes whatever was wrong, so the count
# starting again is the right behaviour rather than a lost record.
_failures = {}


def deliver(messages: list) -> list:
    """
    Hand each message to the app, and report which ones may be confirmed.

    Its own function rather than a block inside the poll loop, so it can be
    run without a network: the rule it carries - what happens to a link that
    cannot be handled - is the one worth being sure about, and a rule that
    can only be exercised by reaching a live relay does not get exercised.

    A message is confirmed only once it has been dealt with. One that throws
    is left unconfirmed so the relay keeps it and hands it back.
    """
    settled = []
    for message in messages:
        nonce = str(message.get("n", ""))
        try:
            handle(nonce, str(message.get("c", "")), via="relay")
        except Exception as exc:                       # noqa: BLE001
            # Kept - but only so many times. One that fails on every attempt
            # would come back forever, quietly, and the holding meant to
            # prevent a silent loss would have become a silent loop instead.
            # After GIVE_UP_AFTER tries it is confirmed anyway and written
            # down as given up on, which is the difference that matters.
            now = time.time()
            record = _failures.setdefault(nonce, [0, now])
            record[0] += 1
            tries, first = record[0], record[1]
            waited = now - first

            if tries < GIVE_UP_AFTER or waited < GIVE_UP_NOT_BEFORE:
                _note({"at": now, "state": "error", "via": "relay",
                       "why": f"could not be handled (try {tries}): {exc}"[:120]})
                continue
            _note({"at": now, "state": "error", "via": "relay",
                   "why": (f"gave up after {tries} tries over "
                           f"{int(waited)}s: {exc}")[:120]})
            _failures.pop(nonce, None)
        else:
            _failures.pop(nonce, None)
        settled.append(nonce)
    return settled


def _confirm(room: str, nonces: list) -> None:
    """
    Tell the relay these are dealt with, by name.

    Named rather than "all of them": a batch can be half handled, and clearing
    the lot because one worked is the same mistake ack=1 exists to fix.
    Failing to confirm costs nothing - the relay keeps them and hands them
    back on the next poll, where the nonce check refuses the duplicates.
    """
    try:
        body = json.dumps({"n": list(nonces)[:50]}).encode("utf-8")
        request = urllib.request.Request(
            f"{relay_base()}/done/{room}", data=body,
            headers=dict(RELAY_HEADERS, **{"Content-Type": "application/json"}))
        with urllib.request.urlopen(request, timeout=15):
            pass
    except Exception:                                  # noqa: BLE001
        pass


def pending() -> dict:
    """
    Is anything of mine still sitting at the relay?

    Asked the practical way round: rather than the PC poking every paired
    phone - which cannot be woken reliably anyway - the PC asks the one place
    that already keeps the queue for seven days. Counts only; the relay cannot
    read the contents and is not asked to hand them over.
    """
    if engine.load_settings().get("share_lan_only"):
        return {"ok": False, "error": "Sharing is set to this network only."}
    try:
        room = load()["room"]
        request = urllib.request.Request(f"{relay_base()}/pending/{room}",
                                         headers=RELAY_HEADERS)
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "error": str(exc)[:140]}

    waiting = int(data.get("waiting") or 0)
    held = int(data.get("held") or 0)
    oldest = float(data.get("oldest") or 0) / 1000.0
    return {
        "ok": True,
        "waiting": waiting,
        "held": held,
        "stuck": waiting + held,
        "oldest": oldest,
        # An older copy of the relay knows neither route; saying so beats
        # reporting a confident zero it never actually answered.
        "since": engine._ago(oldest) if oldest else "",
    }


# --------------------------------------------------------------------------
# A very small WebSocket client, and why it is worth the code
# --------------------------------------------------------------------------
# The long-poll below works, and for a long time the argument for it was that
# a WebSocket needs a library. It does not - this is about eighty lines of
# stdlib - and the argument was costing real money.
#
# A held /wait is, to Cloudflare, "a request still being processed", and a
# Durable Object is billed for wall-clock time whenever it cannot hibernate.
# This account's own analytics for one PC on one day: 6,578 GB-s, 51% of the
# free daily allowance, from a single machine. Two people sharing would have
# stopped the relay outright, with no warning and no bill to explain it.
#
# A hibernating WebSocket does not hold the object awake: the client stays
# connected while the object sleeps, and duration is billed only for the
# moments it is handling something.

class _Wire:
    """One WebSocket connection, text frames only. TLS when the URL says so."""

    def __init__(self, url: str, timeout: float = 30.0):
        parts = urlsplit(url)
        secure = parts.scheme in ("wss", "https")
        port = parts.port or (443 if secure else 80)
        raw = socket.create_connection((parts.hostname, port), timeout=timeout)
        if secure:
            context = ssl.create_default_context()
            raw = context.wrap_socket(raw, server_hostname=parts.hostname)
        self.sock = raw
        self.sock.settimeout(timeout)

        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parts.hostname}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"User-Agent: {RELAY_HEADERS.get('User-Agent', 'Riplox')}\r\n\r\n"
        ).encode())

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(1)
            if not chunk:
                raise OSError("the relay closed the connection")
            header += chunk
            if len(header) > 8192:
                raise OSError("the relay sent a header that made no sense")
        status_line = header.split(b"\r\n")[0].decode("ascii", "replace")
        if " 101 " not in status_line:
            # Named, because "the socket did not open" with no reason is how a
            # relay that has not been redeployed yet looks like a broken app.
            raise OSError(f"the relay refused the socket: {status_line[:60]}")

    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise OSError("the relay connection ended")
            out += chunk
        return out

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])              # FIN + text
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)        # a client frame must be masked
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header)
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> str:
        """One message. Answers pings itself, or the relay hangs up."""
        message = b""
        while True:
            first, second = self._read(2)
            fin, opcode = first & 0x80, first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = int.from_bytes(self._read(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read(8), "big")
            payload = self._read(length) if length else b""

            if opcode == 0x8:
                raise OSError("the relay closed the socket")
            if opcode == 0x9:                   # ping
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0xA:                   # pong
                continue

            message += payload
            if fin:
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _socket_url(room: str) -> str:
    base = relay_base()
    scheme = "wss" if base.startswith("https") else "ws"
    return f"{scheme}://{base.split('://', 1)[-1]}/ws/{room}"


def _run_socket(room: str) -> bool:
    """
    Hold one socket and deal with whatever arrives. Returns True if it worked
    at all, so the caller can tell "the relay has no /ws yet" from "it dropped".
    """
    wire = _Wire(_socket_url(room), timeout=SOCKET_IDLE + 15)
    proven = False
    try:
        wire.send(json.dumps({"hello": 1}))
        _status["relay"] = "connected"
        _status["error"] = ""

        while not _stop.is_set():
            try:
                raw = wire.recv()
            except socket.timeout:
                # Nothing for a while. Say hello again rather than sitting
                # here: it costs one small message and proves the line is up.
                wire.send(json.dumps({"hello": 1}))
                continue

            proven = True
            try:
                data = json.loads(raw)
            except ValueError:
                continue

            settled = deliver(data.get("msgs") or [])
            if settled:
                wire.send(json.dumps({"done": settled}))
            elif data.get("msgs"):
                # Held rather than dealt with - same rule as the poll loop:
                # pause instead of asking again in a tight circle.
                _stop.wait(min(POLL_SECONDS, 20))
                wire.send(json.dumps({"hello": 1}))
    finally:
        wire.close()
    return proven


def _poll_relay() -> None:
    """
    Listen for links, by socket if the relay has one and by long-poll if not.

    The socket is tried first and is what a current relay answers. The poll is
    kept because it is what an older deployment understands, and because a
    network that blocks WebSocket upgrades is a real thing - falling back
    means such a machine keeps working rather than silently receiving nothing.
    """
    backoff = 2
    proven = False
    # Set once the relay proves it has no socket route, so a machine talking to
    # an older deployment does not pay for a failed upgrade every time round.
    no_socket = False

    while not _stop.is_set():
        if engine.load_settings().get("share_lan_only"):
            _status["relay"] = "off (home network only)"
            _stop.wait(5)
            continue

        room = load()["room"]

        if not no_socket:
            _status["relay"] = "connecting"
            try:
                worked = _run_socket(room)
                # It ran and then ended - a dropped connection, not a refusal.
                proven = proven or worked
                backoff = 2 if worked else min(backoff * 2, 60)
                _stop.wait(1 if worked else backoff)
                continue
            except OSError as exc:
                text = str(exc)
                refused = ("refused the socket" in text
                           or "426" in text or "404" in text or "400" in text)
                if refused:
                    # An older relay, or something in the way that will not
                    # pass an upgrade. Fall through to the poll and stay there.
                    no_socket = True
                    _status["error"] = ""
                else:
                    _status["relay"] = "not connected"
                    _status["error"] = text[:120]
                    proven = False
                    _stop.wait(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
            except Exception as exc:                       # noqa: BLE001
                _status["relay"] = "not connected"
                _status["error"] = str(exc)[:120]
                _stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue

        # The first request is deliberately short. A held request answers
        # nothing for 25 seconds, so without this the screen would read "not
        # connected" for 25 seconds after switching Sharing on - which is
        # indistinguishable from broken.
        hold = POLL_SECONDS if proven else 2
        if not proven:
            _status["relay"] = "connecting"

        # No cursor on purpose - see the note in the Worker. The relay hands
        # over whatever is waiting; a repeat is refused here by nonce.
        #
        # ack=1 says: do not treat the next poll as proof this batch landed.
        # Without it the relay drops a message the moment we ask again, so a
        # copy that received a link and then failed to queue it lost the link
        # for good. Now each one is confirmed by name, after it is dealt with.
        url = f"{relay_base()}/wait/{room}?hold={hold}&ack=1"
        try:
            request = urllib.request.Request(url, headers=RELAY_HEADERS)
            with urllib.request.urlopen(request, timeout=hold + 10) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            _status["relay"] = "connected"
            _status["error"] = ""
            proven = True
            backoff = 2

            arrived = data.get("msgs") or []
            settled = deliver(arrived)
            if settled:
                _confirm(room, settled)

            # Messages that arrived and none of them dealt with: the relay is
            # holding them and will hand back the same ones the instant this
            # asks again. Without a pause that is a loop with no wait in it -
            # it burns a core, fills the log, and spends the retry budget in
            # milliseconds on a problem that needs seconds to clear.
            if arrived and not settled:
                _stop.wait(min(POLL_SECONDS, 20))

        except urllib.error.HTTPError as exc:
            _status["relay"] = f"relay said {exc.code}"
            proven = False
            _stop.wait(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as exc:
            # Offline, asleep, or no relay deployed yet. None of these are
            # worth a popup; the screen shows the state and carries on.
            _status["relay"] = "not connected"
            _status["error"] = str(exc)[:120]
            proven = False
            _stop.wait(backoff)
            backoff = min(backoff * 2, 60)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

def start() -> None:
    """Begin listening. Safe to call more than once."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    _stop.clear()
    threading.Thread(target=_serve_lan, name="riplox-lan", daemon=True).start()
    threading.Thread(target=_poll_relay, name="riplox-relay", daemon=True).start()


def stop() -> None:
    global _started, _lan_server
    _stop.set()
    with _lock:
        _started = False
    if _lan_server is not None:
        try:
            _lan_server.shutdown()
        except Exception:
            pass
        _lan_server = None
    _status["lan"] = _status["relay"] = "off"


def apply_setting(on: bool) -> None:
    if on:
        start()
    else:
        stop()


def state() -> dict:
    """Everything the Sharing screen shows. Never a key."""
    data = load()
    invite = data.get("invite")
    settings = engine.load_settings()
    written = _written_by_device()
    return {
        "on": bool(settings.get("sharing")),
        "crypto": AESGCM is not None,
        "lan_only": bool(settings.get("share_lan_only")),
        "approve": bool(settings.get("share_approve")),
        "relay": _status["relay"],
        "lan": _status["lan"],
        "lan_ip": lan_ip(),
        "lan_port": LAN_PORT,
        "error": _status["error"],
        "invite_live": bool(invite and invite.get("expires", 0) > time.time()),
        "invite_life": INVITE_LIFE,
        "qualities": [{"id": q, "label": engine.QUALITY_LABELS[q]}
                      for q in _QUALITY_ORDER],
        "sites": list(SITES),
        # One pass over the history for every device, not one per device -
        # this screen refreshes every three seconds.
        "devices": [
            {"id": d["id"], "name": d["name"], "added": d["added"],
             "last": d.get("last", 0), "count": d.get("count", 0),
             "paused": bool(d.get("paused")),
             "limits": d.get("limits") or {},
             "used": _summary(d, written)}
            for d in data.get("devices", [])
        ],
        "log": _for_screen(data.get("log", []))[:20],
    }


def _for_screen(log: list) -> list:
    """
    The log as the window may see it: no sealed text, nothing expired.

    The sealed blob is useless without this Windows account, but there is no
    reason for it to travel to the page at all - the screen shows that text
    arrived and how long it was, and the text itself is fetched only when
    somebody presses Copy. Expired entries are hidden here and swept from disk
    by _sweep_text; hiding first means an expired secret cannot be shown even
    if the sweep has not run yet.
    """
    now = time.time()
    out = []
    for entry in log:
        if entry.get("kind") == "text":
            if float(entry.get("expires") or 0) <= now:
                continue
            entry = {k: v for k, v in entry.items() if k != "sealed"}
        out.append(entry)
    return out


def _sweep_text() -> None:
    """Drop text whose time is up. Called where the log is already being read."""
    data = load()
    log = data.get("log", [])
    now = time.time()
    kept = [e for e in log
            if e.get("kind") != "text" or float(e.get("expires") or 0) > now]
    if len(kept) != len(log):
        data["log"] = kept
        save(data)


def take_text(entry_id: str) -> str:
    """
    Hand over one piece of sent text, once, and forget it.

    Removed as it is handed over rather than left with a "collected" mark: the
    point of the expiry is that a password does not sit around, and the moment
    it has been taken is the earliest it can stop sitting around.
    """
    data = load()
    log = data.get("log", [])
    for i, entry in enumerate(log):
        if entry.get("id") != entry_id or entry.get("kind") != "text":
            continue
        if entry.get("state") == "waiting":
            return ""                    # not approved yet; nothing to give
        if float(entry.get("expires") or 0) <= time.time():
            break                        # too late; fall through to the sweep
        text = cookies.unseal(entry.get("sealed") or "")
        del log[i]
        data["log"] = log
        save(data)
        return text
    _sweep_text()
    return ""


def _summary(device, written) -> dict:
    today, bytes_written = _usage(device, written)
    return {"today": today, "bytes": bytes_written}


def crypto_available() -> bool:
    """No AES-GCM, no pairing. Nothing here falls back to sending in the open."""
    return AESGCM is not None


def clear_log() -> None:
    data = load()
    data["log"] = []
    save(data)
