"""
Riplox Send for Windows - talking to the PC that does the downloading.

The same sealed envelope the phone app sends, byte for byte: AES-GCM with a
12-byte nonce and a 128-bit tag, base64url without padding. The receiving PC
was not changed to accept this app - it already speaks exactly this, which is
why nothing on that side had to be touched.

Two ways out, tried in this order:

  * the local network, when the PC's address is known. It is instant, nothing
    leaves the building, and it works with no internet at all. A browser page
    can never do this - an HTTPS page is not allowed to call a plain-HTTP
    address on the LAN - which is the whole reason a native sender is worth
    having.

  * the relay, which works from anywhere and holds the message for seven days
    if the PC is switched off. It carries ciphertext and cannot read it.

The relay never learns the key: pairing is parsed here, on this machine.
"""

import base64
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RELAY = "https://relay.xniperbuilds.com"
LAN_PORT = 47811                  # fixed on the PC side, so it can be found
LAN_TIMEOUT = 1.5                 # a machine on the same network answers fast
HOLD = 12                         # seconds to wait for the PC's own verdict


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def new_key() -> str:
    """This machine's own key. Made here, never taken from the pairing link."""
    return b64(secrets.token_bytes(32))


# --------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------

def seal(key_b64: str, body: dict) -> dict:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(unb64(key_b64)).encrypt(
        nonce, json.dumps(body).encode("utf-8"), None)
    return {"n": b64(nonce), "c": b64(sealed)}


def unseal(key_b64: str, nonce_b64: str, cipher_b64: str):
    """The PC's reply, or None when it was not meant for us."""
    try:
        plain = AESGCM(unb64(key_b64)).decrypt(
            unb64(nonce_b64), unb64(cipher_b64), None)
        return json.loads(plain.decode("utf-8"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------

def read_invite(text: str):
    """
    Understand either form the PC offers: the pairing link, or the typed code.

    The link is worth preferring - it carries the PC's address on the local
    network as well, which the typed code has no room for. With it, sending
    never has to leave the building.

    Returns {room, key, code, lan} or None.
    """
    text = (text or "").strip()
    if not text:
        return None

    lan = ""
    if "#" in text and "/p/" in text:
        head, _, fragment = text.partition("#")
        room = head.rstrip("/").rsplit("/", 1)[-1]
        parts = dict(p.split("=", 1) for p in fragment.split("&") if "=" in p)
        key, code, lan = parts.get("k", ""), parts.get("c", ""), parts.get("l", "")
    else:
        bits = text.split(".")
        if len(bits) != 3:
            return None
        room, key, code = bits

    room, key, code = room.strip(), key.strip(), code.strip()
    if not (room and key and code) or len(room) < 16:
        return None
    return {"room": room, "key": key, "code": code, "lan": lan.strip()}


def pair(invite: dict, name: str = "") -> dict:
    """
    Say hello with a key of our own, sealed under the invite's key.

    The key printed in the pairing link is spent the moment it is used, so a
    link found later in a chat or a screenshot opens nothing. Without this the
    link would *be* the device, for good.

    Returns {ok, why, room, key, lan}.
    """
    own = new_key()
    answer = deliver(invite["room"], invite["key"], invite.get("lan", ""),
                     {"kind": "hello", "code": invite["code"],
                      "key": own, "name": (name or "")[:24]})

    if answer.get("why") == "paired" or answer.get("ok"):
        return {"ok": True, "why": "paired", "room": invite["room"],
                "key": own, "lan": invite.get("lan", "")}
    return {"ok": False, "why": answer.get("why", "")}


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

def _post(url: str, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 # Cloudflare answers the default urllib agent with a 403,
                 # which looked exactly like a PC that was switched off.
                 "User-Agent": "RiploxSend/1.0 (+https://xniperbuilds.com)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "RiploxSend/1.0 (+https://xniperbuilds.com)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def lan_alive(lan: str, room: str) -> bool:
    """Is the PC on this network, and is it the one we are paired with?"""
    if not lan:
        return False
    try:
        said = _get(f"http://{lan}/lan-ping", LAN_TIMEOUT)
        return bool(said.get("ok")) and said.get("room") == room
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        return False


def deliver(room: str, key: str, lan: str, body: dict) -> dict:
    """
    Seal this and get it to the PC. Returns {ok, why, via}.

    `why` is the PC's own word - queued, held, paused, day-limit, revoked and
    so on - never the relay's guess. The relay cannot read the message, so all
    it could ever honestly say is that it took it.
    """
    rid = b64(secrets.token_bytes(12))
    body = dict(body, r=rid, ts=time.time())
    envelope = seal(key, body)

    # The local network first: no round trip to another continent, and the
    # answer is the PC's own, immediately.
    if lan_alive(lan, room):
        try:
            said = _post(f"http://{lan}/lan-send", envelope, 8)
            why = said.get("why", "")
            if why and why != "unknown":
                return {"ok": bool(said.get("ok")), "why": why, "via": "lan"}
        except (urllib.error.URLError, OSError, ValueError, socket.timeout):
            pass                      # fall through to the relay, quietly

    try:
        said = _post(f"{RELAY}/send/{room}", envelope, 15)
    except (urllib.error.URLError, OSError, ValueError, socket.timeout) as exc:
        return {"ok": False, "why": "", "via": "relay", "error": str(exc)[:120]}

    if not said.get("ok"):
        return {"ok": False, "why": "", "via": "relay",
                "error": "the relay would not take it"}

    # The relay is a postbox; the verdict comes back separately, sealed under
    # the same key. Silence is not a failure - the message is still in the box.
    try:
        got = _get(f"{RELAY}/ack/{room}?r={rid}&hold={HOLD}", HOLD + 8)
        if got.get("ok") and got.get("ack"):
            said_back = unseal(key, got["ack"].get("n", ""), got["ack"].get("c", ""))
            if said_back:
                return {"ok": True, "why": said_back.get("why", ""), "via": "relay"}
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        pass

    return {"ok": True, "why": "", "via": "relay"}


def send_link(room: str, key: str, lan: str, url: str, quality: str = "") -> dict:
    body = {"kind": "link", "url": url}
    if quality:
        body["quality"] = quality
    return deliver(room, key, lan, body)


def ping(room: str, key: str, lan: str) -> dict:
    """Is the PC on, and does it still know this machine?"""
    return deliver(room, key, lan, {"kind": "ping"})


# --------------------------------------------------------------------------
# Plain English
# --------------------------------------------------------------------------

WORDS = {
    "queued": "Downloading on your PC",
    "held": "Sent - waiting for approval on the PC",
    "paired": "Paired",
    "pong": "Ready",
    "paused": "This machine is paused on the PC",
    "site": "This machine is not allowed to send that site",
    "day-limit": "Today's downloads are used up",
    "total-limit": "This machine's allowance is used up",
    "replay": "That one was already sent",
    "stale": "This machine's clock is too far ahead",
    "expired": "That pairing code has expired",
    "used": "That pairing code was already used",
    "revoked": "Your PC removed this machine - ask for a new code",
    "unknown": "Your PC did not recognise this machine",
    "bad-link": "That link was refused",
}


def words(why: str) -> str:
    return WORDS.get(why or "", "")


def guess_name() -> str:
    """A name the PC can show before the host types one of their own."""
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC")[:24]
