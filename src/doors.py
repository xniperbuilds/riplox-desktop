"""
Riplox Desktop - the second door.

Every desktop downloader worth the name is built on yt-dlp, which means when
yt-dlp is refused by a site, all of them fail on the same day. That is not a
theory: it is what a TikTok link did here for a week, and the only advice on
offer was to try a different connection, which did not help either.

So there is a second way in. yt-dlp stays the first and the general one - it
knows a thousand sites and does them properly. This module knows four, and its
whole job is to still work on the day yt-dlp does not. It is tried only after
yt-dlp has actually failed, so a working download never comes through here.

Deliberately dependency-free: standard library only, no browser, no signing,
no TLS impersonation. Everything here is a plain request with the headers a
browser would send and a cookie jar that keeps what the site hands back. That
is what made TikTok answer when nine cleverer attempts did not.

Nothing in here is copied from another project. The addresses, JSON paths and
header names are facts about the sites; the code is Riplox's own.
"""

import html
import json
import re
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar, MozillaCookieJar
from urllib.parse import urlsplit

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

PAGE_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

_TIMEOUT = 20
_MAX_PAGE = 8 * 1024 * 1024          # a video page is under 1 MB; this is a cap


class DoorError(Exception):
    """Something this door can say to the user as it stands."""


# --------------------------------------------------------------------------
# Going out through a proxy
# --------------------------------------------------------------------------
# Set by the engine before this module is asked for anything, because a proxy
# that the rest of the app obeys and this one does not is worse than no proxy
# at all: the user believes every request is going out through it, and then
# the fallback route - the one that runs precisely when a site is refusing
# this connection - goes straight out and shows them the address they were
# hiding. So the rule here is all or nothing.
#
# SOCKS is deliberately not attempted. urllib speaks http and https proxies on
# its own and needs a third-party library for SOCKS; rather than add one to
# reach a fallback, a SOCKS proxy switches this module off and says why.

# Per thread, not per module. Several downloads run at once, each in its own
# worker thread, and each configures this before it resolves. A single shared
# value means one thread can overwrite another's a moment before it is read -
# and the case that loses is a download that was meant to go through a proxy
# quietly going out direct, which is the one failure this whole section exists
# to prevent. Thread-local costs nothing and removes the race entirely.
_local = threading.local()


def configure(proxy: str = "") -> None:
    """Tell this module which proxy the rest of the app is using."""
    _local.proxy = str(proxy or "").strip()


def _proxy() -> str:
    return getattr(_local, "proxy", "")


def proxy_problem() -> str:
    """Why this module cannot honour the configured proxy, or ""."""
    current = _proxy()
    if not current:
        return ""
    scheme = current.split("://", 1)[0].lower() if "://" in current else ""
    if scheme in ("http", "https"):
        return ""
    return (f"Riplox's own route cannot go out through a {scheme or 'that'} "
            f"proxy, and it will not go around one. Use an http:// or "
            f"https:// proxy if you want the fallback route as well.")


def _proxy_handler() -> list:
    """The handler list that sends this module's requests the same way."""
    current = _proxy()
    if not current:
        return []
    if proxy_problem():
        # Unreachable in normal use - resolve() refuses first. Belt and braces,
        # because the failure it guards against is a leaked address.
        raise DoorError(proxy_problem())
    return [urllib.request.ProxyHandler({"http": current, "https": current})]


# --------------------------------------------------------------------------
# Where an address is allowed to point
# --------------------------------------------------------------------------
# Every address here is read out of a page with a regular expression, and these
# pages carry text other people wrote - captions, comments, group posts. The
# patterns do not know the difference between the site's own JSON and a comment
# that happens to contain the same words, so a single line typed under a video
# is enough to hand this module an address of someone else's choosing.
#
# Measured before this existed: a comment reading "playable_url":"file:///C:/..."
# was picked up and would have been fetched. Also accepted were 127.0.0.1
# (Riplox's own API), 169.254.169.254 (the cloud metadata address), and any
# third-party host at all.
#
# So an address is only used when it is https and lands on the site's own
# network. A new CDN name will need adding here, and that is the intended
# trade: a door that stops working is a bug report, a door that fetches
# whatever a stranger wrote is not.

# Written as patterns rather than a list of names on purpose. These CDNs are
# named by region and the regions keep arriving: a list of exact names looks
# safe and then refuses a real video the day TikTok answers from a new one, and
# nobody would connect the two. Checked while writing this - a hand-written
# list had already missed tiktokcdn-eu.com, which is a live host today.
#
# What each pattern still guarantees: the registrable domain belongs to the
# site. A regional suffix is allowed to vary; the brand is not.
_CDN = {
    "tiktok": re.compile(
        r"(tiktok\.com|tiktokcdn(-[a-z0-9]+)?\.(com|net)|tiktokv\.(com|us|eu)"
        r"|ttwstatic\.com|muscdn\.com|byteoversea\.com|ibyteimg\.com"
        r"|akamaized\.net)$"),
    "instagram": re.compile(r"(cdninstagram\.com|instagram\.com|fbcdn\.net)$"),
    "facebook": re.compile(r"(fbcdn\.net|facebook\.com|fbsbx\.com)$"),
    "youtube": re.compile(r"(googlevideo\.com|youtube\.com|ytimg\.com)$"),
}
# akamaized.net is a shared CDN rather than TikTok's own, and it is here
# because TikTok genuinely serves from it. It is the weakest entry in the
# table: it would also allow another Akamai customer's file. Kept because a
# refused real video is the more likely harm by a wide margin, and reaching it
# still requires an Akamai account and a signed path.


def _address_ok(raw: str, site: str) -> bool:
    """Is this an https address on the site's own network?"""
    try:
        parts = urlsplit(raw or "")
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost":
        return False
    # An address written as a bare IP is never one of these CDNs, and is how
    # every interesting internal target gets reached.
    if re.fullmatch(r"[\d.]+", host) or ":" in host:
        return False
    pattern = _CDN.get(site)
    if pattern is None:
        return False
    # Anchored on a label boundary so tiktok.com.evil.net cannot match: the
    # host must END with the domain, and end with it as a whole label.
    found = pattern.search(host)
    if not found:
        return False
    before = host[:found.start()]
    return before == "" or before.endswith(".")


def _checked(raw: str, site: str) -> str:
    """The address, or a refusal naming what was actually found."""
    if _address_ok(raw, site):
        return raw
    host = ""
    try:
        host = urlsplit(raw or "").hostname or ""
    except ValueError:
        pass
    # Named on purpose. If this site ever serves from a new CDN, this message
    # is the only thing that will say so - and a refusal nobody can read turns
    # into "the door just stopped working" with no way to find out why.
    raise DoorError(
        f"The {site.title()} page offered an address Riplox will not fetch"
        f"{' (' + host + ')' if host else ''}. These pages carry text other "
        f"people wrote, so only the site's own video addresses are used.")


# --------------------------------------------------------------------------
# Which door, if any
# --------------------------------------------------------------------------

def site_of(url: str) -> str:
    """The door that handles this URL, or "" when yt-dlp is on its own."""
    host = (urlsplit(url or "").hostname or "").lower().lstrip(".")
    if not host:
        return ""
    parts = host.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else host
    if root == "tiktok.com":
        return "tiktok"
    if root == "instagram.com":
        return "instagram"
    if root in ("facebook.com", "fb.watch", "fb.com"):
        return "facebook"
    if root in ("youtube.com", "youtu.be", "youtube-nocookie.com"):
        return "youtube"
    return ""


def handles(url: str) -> bool:
    return bool(site_of(url))


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _opener(session_for: str = "") -> urllib.request.OpenerDirector:
    """
    A fresh jar per link, optionally carrying the saved sign-in for a site.

    TikTok answers the first request from an unknown client with a wall page
    and a Set-Cookie, and the second request - carrying that cookie - with the
    real thing. Keeping the jar is the entire trick; a stateless request can
    never get past the first step, which is why so many attempts at this look
    like the site is blocking the machine.

    The jar starts empty by default and that is deliberate. A session is only
    put in when the site has actually said it wants one - see the age-gated
    path below - because signed out is what gets past the wall, and a door
    that always announced who it was would be trading a reliable route for an
    occasional one.
    """
    jar = CookieJar()
    if session_for:
        _load_session(jar, session_for)
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), *_proxy_handler())


def _session_jar(url: str):
    """The saved sign-in for this address as a jar, or None."""
    # Imported here rather than at the top: this module is the fallback that
    # has to keep working when the rest of the app is having a bad day, and
    # the cookie store brings Windows-only machinery with it.
    try:
        import cookies as store
    except ImportError:
        return None

    path = store.materialize(url)          # None when absent or paused
    if not path:
        return None
    try:
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:                      # noqa: BLE001
        return None
    finally:
        # The file is a live session in the clear; it does not outlive this.
        store.release(path)


def _load_session(jar, url: str) -> bool:
    saved = _session_jar(url)
    if saved is None:
        return False
    for cookie in saved:
        jar.set_cookie(cookie)
    return True


def _have_session(url: str) -> bool:
    """Is there a sign-in to offer at all? A paused site counts as none."""
    return _session_jar(url) is not None


def _headers_with_jar(opener, headers: dict) -> dict:
    """
    Fold the jar into a plain Cookie header.

    The address the page gives out is signed and checked against the same
    session that asked for it, so the bytes have to be requested with those
    cookies. Handing them over as a header keeps this module's return value a
    plain dictionary instead of a live object the caller has to keep alive.
    """
    jar = next((h.cookiejar for h in opener.handlers
                if isinstance(h, urllib.request.HTTPCookieProcessor)), None)
    pairs = "; ".join(f"{c.name}={c.value}" for c in (jar or []))
    out = dict(headers)
    if pairs:
        out["Cookie"] = pairs
    return out


def _get(opener, url: str, headers: dict = None, referer: str = "") -> str:
    sent = dict(PAGE_HEADERS)
    if referer:
        sent["Referer"] = referer
    sent.update(headers or {})
    request = urllib.request.Request(url, headers=sent)
    with opener.open(request, timeout=_TIMEOUT) as response:
        raw = response.read(_MAX_PAGE)
    return raw.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# TikTok
# --------------------------------------------------------------------------

_TT_ID = re.compile(r"/(?:video|photo)/(\d+)")
_TT_BLOB = re.compile(
    r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.S)


def _tiktok_id(opener, url: str) -> str:
    """
    The post id, following a vm.tiktok.com / /t/ short link if that is what
    arrived. The share sheet hands out short links far more often than full
    ones, so a door that only understood full ones would miss most of them.
    """
    found = _TT_ID.search(urlsplit(url).path)
    if found:
        return found.group(1)

    try:
        request = urllib.request.Request(url, headers=PAGE_HEADERS)
        with opener.open(request, timeout=_TIMEOUT) as response:
            landed = response.geturl()
    except (urllib.error.URLError, OSError) as exc:
        raise DoorError(f"Could not follow that TikTok link: {exc}") from exc

    found = _TT_ID.search(urlsplit(landed).path)
    if not found:
        raise DoorError("That does not look like a TikTok post link.")
    return found.group(1)


def _tiktok_detail(post_id: str, signed_in: bool = False) -> tuple:
    """
    The post's own data out of the page, plus the jar that got it.

    The address deliberately uses @i rather than the poster's username: the
    numeric id is what identifies the post, the username is decoration, and
    the placeholder form is answered for any post without having to know who
    made it - which a short link does not tell you.

    Every attempt gets a NEW jar, which is the whole trick and was learned the
    hard way. TikTok answers some requests with a ~1.5 KB wall page and others
    with the real thing, at random, from the same machine in the same second -
    measured: a fresh jar was refused three times running while a different
    request moments later came back with 391 KB of page. Crucially the wall
    sets no cookie at all, so retrying on the jar that was just refused is
    retrying with nothing new to say. Rebuilding it is what turns a run of
    failures back into a download.
    """
    url = f"https://www.tiktok.com/@i/video/{post_id}"
    last = ""

    # The refusals arrive in bursts rather than one at a time, so the waits
    # grow: a longer pause is worth more here than another quick attempt.
    # Eleven seconds in the worst case, spent only on a link the engine has
    # already failed to fetch - by which point waiting beats not having it.
    for wait in (0, 0.6, 1.2, 2.0, 3.0, 4.0):
        if wait:
            time.sleep(wait)
        # A clean jar every time - plus the saved sign-in when the post has
        # already told us it will not be handed over without one.
        opener = _opener(_TT_HOME if signed_in else "")
        try:
            page = _get(opener, url, referer="https://www.tiktok.com/")
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
            continue

        blob = _TT_BLOB.search(page)
        if not blob:
            last = (f"TikTok answered with a {len(page)}-byte check page "
                    f"instead of the post")
            continue

        try:
            data = json.loads(html.unescape(blob.group(1)))
        except ValueError:
            last = "the post data could not be read"
            continue

        detail = (data.get("__DEFAULT_SCOPE__") or {}).get("webapp.video-detail")
        if detail:
            return detail, opener
        last = "the page had no post in it"

    # Worth saying plainly: this one is not a verdict on the link. The same
    # link usually works on the next press, because the refusal is aimed at
    # the request rather than at the video.
    raise DoorError(f"TikTok did not hand over the post - {last}. "
                    f"This one often clears on its own, so press retry.")


def _tiktok_playable(item: dict) -> tuple:
    """(address, extension) for the best stream on offer."""
    video = item.get("video") or {}

    # bitrateInfo is where the alternatives live, in the site's own order of
    # preference. The plain playAddr is the same file as the first of them,
    # so it is the fallback rather than the choice.
    for entry in video.get("bitrateInfo") or []:
        addresses = ((entry.get("PlayAddr") or {}).get("UrlList")) or []
        for address in addresses:
            if _address_ok(address, "tiktok"):
                return address, "mp4"

    for key in ("playAddr", "downloadAddr"):
        if video.get(key):
            return _checked(video[key], "tiktok"), "mp4"

    raise DoorError("TikTok did not include a video address for that post.")


_TT_HOME = "https://www.tiktok.com/"


def _tiktok_signed_in(post_id: str, item: dict, opener) -> tuple:
    """
    One more go at an age-gated post, this time carrying the saved sign-in.

    Worth its own attempt rather than a message telling the user to sign in:
    they generally already have. The first pass is signed out on purpose, so
    until now a saved TikTok session was never offered to this route at all -
    the post was refused with "sign in", by the one part of Riplox that never
    tried signing in. Now the refusal only stands if it survives the session.

    Anything that comes back is used with the jar that fetched it: the address
    TikTok hands out is checked against the session that asked for it, so the
    bytes have to be requested the same way.
    """
    if not _have_session(_TT_HOME):
        raise DoorError("TikTok has age-restricted that post. Sign in to "
                        "TikTok in Settings and try again.")

    try:
        detail, signed = _tiktok_detail(post_id, signed_in=True)
    except DoorError:
        detail, signed = {}, None

    fresh = (detail or {}).get("itemInfo", {}).get("itemStruct") or {}
    if fresh.get("video"):
        return fresh, signed

    raise DoorError("TikTok has age-restricted that post and would not hand "
                    "it over even with your saved sign-in. Signing in to "
                    "TikTok again in Settings is the only thing that can "
                    "change this.")


def _tiktok(url: str) -> dict:
    # Following a short link is a separate errand from reading the post, and
    # the jar that ends up mattering is the one the post came back on.
    post_id = _tiktok_id(_opener(), url)
    detail, opener = _tiktok_detail(post_id)

    # The site says so itself rather than by failing later: a removed post and
    # a blocked one are different things and the user deserves to be told
    # which, instead of watching a download fail for no stated reason.
    if detail.get("statusMsg"):
        raise DoorError(f"TikTok says: {detail['statusMsg']}")

    item = detail.get("itemInfo", {}).get("itemStruct") or {}
    if not item:
        raise DoorError("That TikTok post could not be read.")
    if item.get("isContentClassified"):
        item, opener = _tiktok_signed_in(post_id, item, opener)
    if item.get("imagePost"):
        raise DoorError("That is a TikTok photo post, not a video.")

    address, ext = _tiktok_playable(item)
    author = (item.get("author") or {}).get("uniqueId", "")
    return {
        "url": address,
        "id": post_id,
        "ext": ext,
        "title": (item.get("desc") or "").strip() or f"TikTok {post_id}",
        "uploader": author,
        "thumbnail": (item.get("video") or {}).get("cover", ""),
        "duration": (item.get("video") or {}).get("duration", 0),
        # Everything the CDN checks, flattened into plain headers so the
        # caller can fetch the bytes with any client it likes - the jar that
        # got us this far does not have to be carried around to do it.
        "headers": _headers_with_jar(opener, {
            "User-Agent": CHROME_UA,
            "Referer": "https://www.tiktok.com/",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }),
        "site": "TikTok",
    }


# --------------------------------------------------------------------------
# Instagram
# --------------------------------------------------------------------------
# The engine tries exactly one route here, and when a saved session has gone
# stale that route answers 400 and the whole extraction stops - which is how a
# dead login ends up breaking public reels that need no login at all. This door
# is the opposite shape: several routes, tried in turn, none of them signed in.

_IG_CODE = re.compile(r"/(?:reels?|p|tv)/([A-Za-z0-9_-]+)")
_IG_APP_ID = "936619743392459"

# The app's own client string. The mobile API answers a browser with a login
# wall and this with the media, which is the whole reason it is written out.
_IG_ANDROID_UA = ("Instagram 275.0.0.27.98 Android (33/13; 280dpi; 720x1423; "
                  "Xiaomi; Redmi 7; onclite; qcom; en_US; 458229237)")

# Three shapes for the same thing, depending on which route answered.
_IG_VIDEO = (
    re.compile(r'"video_url"\s*:\s*"([^"]+)"'),
    re.compile(r'"video_versions"\s*:\s*\[\s*\{[^}]*?"url"\s*:\s*"([^"]+)"'),
    re.compile(r'"playback_url"\s*:\s*"([^"]+)"'),
)
_IG_THUMB = re.compile(r'"display_url"\s*:\s*"([^"]+)"')
_IG_OWNER = re.compile(r'"username"\s*:\s*"([^"]+)"')
_IG_CAPTION = re.compile(r'"(?:edge_media_to_caption".*?"text"|caption"\s*:\s*'
                         r'\{[^}]*?"text")\s*:?\s*"([^"]{1,300})"', re.S)

# Only the API's own words count as a verdict. The reel page is no help here:
# a page that plays perfectly well carries "sensitive" nine times and "consent"
# nineteen, so matching on those would have labelled every ordinary failure a
# restriction - checked against three working reels rather than assumed.
_IG_WALLED = ("is_content_restricted", "restricted_content",
              "certain audiences", "age_restricted", "login_required")


def _unescape_url(raw: str) -> str:
    return (raw.replace("\\u0026", "&").replace("\\/", "/")
            .replace("&amp;", "&").replace("\\", ""))


def _ig_find(text: str) -> str:
    """
    The first address in the body that is really Instagram's.

    Every match is checked rather than only the first: a caption containing
    the words is enough to produce a match, and stopping at it would hide the
    real address further down the same page.
    """
    for pattern in _IG_VIDEO:
        for found in pattern.finditer(text):
            address = _unescape_url(found.group(1))
            if _address_ok(address, "instagram"):
                return address
    return ""


def _unescape_text(raw: str) -> str:
    """
    Decode the escapes in a JSON string that was pulled out with a regex.

    Through json, not unicode_escape. An emoji arrives as a surrogate pair
    (\\ud83d\\ude02) and unicode_escape decodes each half on its own, leaving
    two lone surrogates that are not a character at all - they survive as far
    as the file name and then raise on the way to disk. json puts the pair back
    together, which is what the site meant by them.
    """
    try:
        return json.loads('"' + raw.replace("\n", " ") + '"')
    except ValueError:
        # Not valid JSON on its own - a stray backslash, a cut-off escape.
        # Keep the readable text rather than dropping the caption entirely.
        return re.sub(r"\\u[0-9a-fA-F]{4}|\\.", "", raw)


def _ig_details(text: str, code: str) -> dict:
    thumb = _IG_THUMB.search(text)
    owner = _IG_OWNER.search(text)
    caption = _IG_CAPTION.search(text)
    title = ""
    if caption:
        title = _unescape_text(caption.group(1))
        title = re.sub(r"\s+", " ", title).strip()[:120]
    return {
        "title": title or f"Instagram {code}",
        "uploader": owner.group(1) if owner else "",
        "thumbnail": _unescape_url(thumb.group(1)) if thumb else "",
    }


def _ig_page(opener, code: str) -> str:
    """The reel's own page. Signed out, this is the route that works."""
    return _get(opener, f"https://www.instagram.com/reel/{code}/",
                headers={"x-ig-app-id": _IG_APP_ID},
                referer="https://www.instagram.com/")


def _ig_embed(opener, code: str) -> str:
    """The embed Instagram publishes for other sites to use."""
    return _get(opener, f"https://www.instagram.com/p/{code}/embed/captioned/",
                headers={"x-ig-app-id": _IG_APP_ID},
                referer="https://www.instagram.com/")


def _ig_mobile(opener, code: str) -> str:
    """
    The app's own API, reached through the id oEmbed hands out.

    Two requests rather than one because the API is keyed on a numeric media
    id and a link only carries the short code.
    """
    oembed = _get(
        opener,
        "https://i.instagram.com/api/v1/oembed/?url="
        f"https://www.instagram.com/p/{code}/",
        headers={"User-Agent": _IG_ANDROID_UA, "x-ig-app-id": _IG_APP_ID})
    found = re.search(r'"media_id"\s*:\s*"?(\d+)', oembed)
    if not found:
        return ""

    return _get(
        opener,
        f"https://i.instagram.com/api/v1/media/{found.group(1)}/info/",
        headers={
            "User-Agent": _IG_ANDROID_UA,
            "x-ig-app-id": _IG_APP_ID,
            "x-ig-app-locale": "en_US",
            "x-ig-device-locale": "en_US",
            "x-ig-mapped-locale": "en_US",
            "x-fb-http-engine": "Liger",
            "x-fb-client-ip": "True",
            "x-fb-server-cluster": "True",
        })


def _instagram(url: str) -> dict:
    found = _IG_CODE.search(urlsplit(url).path)
    if not found:
        raise DoorError("That does not look like an Instagram post or reel link.")
    code = found.group(1)

    opener = _opener()
    walled = False
    for route in (_ig_page, _ig_embed, _ig_mobile):
        try:
            body = route(opener, code)
        except (urllib.error.URLError, OSError):
            continue                     # this route is out; the next may not be
        if not body:
            continue

        address = _ig_find(body)
        if address:
            info = _ig_details(body, code)
            info.update({
                "url": address,
                "id": code,
                "ext": "mp4",
                "headers": _headers_with_jar(opener, {
                    "User-Agent": CHROME_UA,
                    "Referer": "https://www.instagram.com/",
                    "Accept": "*/*",
                }),
                "site": "Instagram",
            })
            return info

        low = body.lower()
        walled = walled or any(mark in low for mark in _IG_WALLED)

    if walled:
        raise DoorError(
            "Instagram says this one is restricted, so no signed-out route can "
            "reach it. Sign in to Instagram in Settings and try again.")
    # Deliberately not guessing which of these it is. Instagram gives no
    # signal that separates them from outside, and naming the wrong one sends
    # someone off to fix something that was never the problem.
    raise DoorError("Instagram returned the page but no video in it. That "
                    "usually means the account is private, the post was "
                    "removed, or Instagram wants a signed-in account for it.")


# --------------------------------------------------------------------------
# Facebook
# --------------------------------------------------------------------------
# Facebook serves a different page to almost every client it can tell apart,
# and the signed-out desktop one is mostly a login prompt. The stripped-down
# mobile pages are the ones that still answer plainly, which is why they are
# tried first here rather than as a fallback.

_FB_ID = re.compile(r"(?:/videos?/(?:[^/]+/)?(\d+)|[?&]v=(\d+)|/reel/(\d+))")

# The same address under several names, depending on which page answered.
_FB_VIDEO = (
    re.compile(r'"playable_url_quality_hd"\s*:\s*"([^"]+)"'),
    re.compile(r'"browser_native_hd_url"\s*:\s*"([^"]+)"'),
    re.compile(r'"playable_url"\s*:\s*"([^"]+)"'),
    re.compile(r'"browser_native_sd_url"\s*:\s*"([^"]+)"'),
    re.compile(r'"hd_src(?:_no_ratelimit)?"\s*:\s*"([^"]+)"'),
    re.compile(r'"sd_src(?:_no_ratelimit)?"\s*:\s*"([^"]+)"'),
)
_FB_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
_FB_THUMB = re.compile(r'<meta property="og:image" content="([^"]*)"')
_FB_OWNER = re.compile(r'"owner"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]{1,80})"')

# Facebook's own words for "you are not getting this while signed out".
_FB_WALLED = ("you must log in", "log in to continue", "log into facebook",
              "content isn't available", "this content isn't available right now")


def _fb_id(url: str) -> str:
    found = _FB_ID.search(url)
    return next((g for g in found.groups() if g), "") if found else ""


def _facebook(url: str) -> dict:
    opener = _opener()

    # A share link (fb.watch, /share/v/...) only becomes an id after the
    # redirect, so it is followed before anything else is decided.
    landed = url
    if "fb.watch" in url or "/share/" in url:
        try:
            request = urllib.request.Request(url, headers=PAGE_HEADERS)
            with opener.open(request, timeout=_TIMEOUT) as response:
                landed = response.geturl()
        except (urllib.error.URLError, OSError) as exc:
            raise DoorError(f"Could not follow that Facebook link: {exc}") from exc

    video_id = _fb_id(landed) or _fb_id(url)

    # A page, a profile or a video listing is not a request for a video, and
    # Facebook puts dozens of playable addresses on all three. Without this,
    # pasting facebook.com/SomePage/ returned *a* video carrying the page's
    # title - a file the user never asked for, with nothing to tell them so.
    # Measured on a real page before this guard existed. Refusing is the only
    # honest answer: there is no "the" video on a page that lists many.
    if not video_id:
        raise DoorError(
            "That Facebook link points at a page rather than one video. Open "
            "the video itself and copy the link from there - a page can hold "
            "hundreds, and Riplox will not guess which one you meant.")

    walled = False
    pages = [
        # Stripped-down first: these still answer a signed-out reader.
        landed.replace("://www.", "://m.").replace("://web.", "://m."),
        landed.replace("://www.", "://mbasic.").replace("://web.", "://mbasic."),
        landed,
    ]
    if video_id:
        pages.insert(0, f"https://m.facebook.com/watch/?v={video_id}")

    seen = set()
    for page_url in pages:
        if page_url in seen:
            continue
        seen.add(page_url)
        try:
            body = _get(opener, page_url, referer="https://www.facebook.com/")
        except (urllib.error.URLError, OSError):
            continue
        if not body:
            continue

        # Every match, not the first: a group post or a comment can contain
        # the same words, and the real address is often further down the
        # same page.
        address = ""
        for pattern in _FB_VIDEO:
            for found in pattern.finditer(body):
                candidate = _unescape_url(found.group(1))
                if _address_ok(candidate, "facebook"):
                    address = candidate
                    break
            if address:
                break

        if address:
            title = _FB_TITLE.search(body)
            thumb = _FB_THUMB.search(body)
            owner = _FB_OWNER.search(body)
            return {
                "url": address,
                "id": video_id or "facebook",
                "ext": "mp4",
                "title": (html.unescape(title.group(1)).strip()
                          if title else f"Facebook {video_id or 'video'}")[:120],
                "uploader": html.unescape(owner.group(1)) if owner else "",
                "thumbnail": _unescape_url(thumb.group(1)) if thumb else "",
                "headers": _headers_with_jar(opener, {
                    "User-Agent": CHROME_UA,
                    "Referer": "https://www.facebook.com/",
                    "Accept": "*/*",
                }),
                "site": "Facebook",
            }

        low = body.lower()
        walled = walled or any(mark in low for mark in _FB_WALLED)

    if walled:
        raise DoorError(
            "Facebook wants an account signed in for this one. Sign in to "
            "Facebook in Settings and try again.")
    raise DoorError("Facebook did not return a video for that link. Private "
                    "groups and friends-only posts cannot be reached.")


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------
# The one that matters most and was the last to get a door, because YouTube is
# the site yt-dlp handles best - so a second way in here is insurance rather
# than a fix for something failing today.
#
# What it is insurance against is named and public: YouTube is moving its
# streams onto SABR, its own delivery protocol, and yt-dlp's downloader for it
# has been an open pull request for over a year. The day the remaining clients
# are pushed across, every downloader built on yt-dlp stops at the same hour.
#
# The route here is the app's own player endpoint. Measured on 2026-08-18 from
# this machine, no sign-in, standard library only:
#
#   IOS      27 adaptive formats, every one with a plain url, up to 2160p
#   ANDROID  1 muxed (itag 18, 360p) + 29 adaptive, all plain, up to 2160p
#   WEB      UNPLAYABLE  - "Video unavailable"
#   MWEB     UNPLAYABLE  - "The page needs to be reloaded"
#   TVHTML5  ERROR       - "no longer supported in this application or device"
#
# The two that answer hand the addresses over directly: nothing has to be
# deciphered, no proof-of-origin token is needed, and no JavaScript runs. That
# is the whole reason this door can exist in a module with no dependencies.
#
# Signed-in YouTube is deliberately not attempted. This endpoint does not take
# a cookie jar the way the other three doors do - it wants a SAPISIDHASH
# authorisation header computed per request - so a private or members-only
# video is refused here and says so.
# lazy: signed-out only. If members-only videos ever need this route, the
# upgrade is the SAPISIDHASH header built from the saved SAPISID cookie.

_YT_PLAYER = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"

# An id is eleven characters and appears in five different shapes of link.
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YT_IN_PATH = re.compile(r"/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})")

# Order matters and it is not the same in both cases - see _youtube. IOS is
# the richer answer; ANDROID is the only one of the two that still offers a
# muxed stream, which is all there is to work with when ffmpeg is absent.
_YT_CLIENTS = {
    "IOS": {
        "context": {"client": {
            "clientName": "IOS", "clientVersion": "20.10.4",
            "deviceMake": "Apple", "deviceModel": "iPhone16,2",
            "osName": "iPhone", "osVersion": "18.3.2.22D82",
            "hl": "en", "gl": "US"}},
        "ua": ("com.google.ios.youtube/20.10.4 (iPhone16,2; U; "
               "CPU iOS 18_3_2 like Mac OS X;)"),
    },
    "ANDROID": {
        "context": {"client": {
            "clientName": "ANDROID", "clientVersion": "20.10.38",
            "androidSdkVersion": 34, "osName": "Android", "osVersion": "14",
            "hl": "en", "gl": "US"}},
        "ua": "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip",
    },
}


def _yt_id(url: str) -> str:
    """The video id out of any of the shapes YouTube hands out."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()

    if host.endswith("youtu.be"):
        tail = parts.path.strip("/").split("/")[0]
        if _YT_ID.fullmatch(tail):
            return tail

    for pair in parts.query.split("&"):
        if pair.startswith("v=") and _YT_ID.fullmatch(pair[2:]):
            return pair[2:]

    found = _YT_IN_PATH.search(parts.path)
    if found:
        return found.group(1)

    raise DoorError("That does not look like a link to one YouTube video. A "
                    "channel or a playlist page has to be opened first - a "
                    "page can hold hundreds, and Riplox will not guess.")


def _post_json(url: str, body: dict, user_agent: str) -> dict:
    """A plain JSON POST. Its own helper because _get only speaks GET."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.youtube.com",
        })
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()), *_proxy_handler())
    with opener.open(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read(_MAX_PAGE))


def _yt_ask(video_id: str, client: str) -> dict:
    """What one client is told about this video."""
    spec = _YT_CLIENTS[client]
    return _post_json(_YT_PLAYER, {
        "context": spec["context"],
        "videoId": video_id,
        # Both of these say "yes, show it anyway" to content YouTube would
        # otherwise interrupt with a confirmation the app answers on the
        # viewer's behalf. Without them an ordinary flagged video comes back
        # unplayable for no stated reason.
        "contentCheckOk": True,
        "racyCheckOk": True,
    }, spec["ua"])


def _yt_refusal(status: dict) -> str:
    """YouTube's own words for why not, turned into one readable line."""
    reason = (status.get("reason") or "").strip()
    if not reason:
        # Sometimes the sentence is only in the panel behind the reason.
        renderer = ((status.get("errorScreen") or {})
                    .get("playerErrorMessageRenderer") or {})
        reason = ((renderer.get("reason") or {}).get("simpleText") or "").strip()

    state = status.get("status") or "refused"
    if state == "LOGIN_REQUIRED":
        return ("YouTube wants an account signed in for that video, and "
                "Riplox's own route to YouTube is signed out by design. "
                + (reason or "It is usually private, members-only, or age-restricted."))
    return f"YouTube says: {reason or state.lower().replace('_', ' ')}."


def _yt_kind(entry: dict) -> str:
    mime = entry.get("mimeType") or ""
    return mime.split("/", 1)[0]


def _yt_is_mp4(entry: dict) -> bool:
    """An mp4 container carrying H.264 - the pair that merges with -c copy."""
    mime = entry.get("mimeType") or ""
    return "mp4" in mime and ("avc1" in mime or "mp4a" in mime)


def _yt_pick_video(entries: list, cap: int, prefer_h264: bool) -> dict:
    """
    The best video stream at or under the height that was asked for.

    Sorted rather than filtered down to one rule so that a request for 1080p
    on a video that only has 720p still comes back with the 720p, which is
    what somebody asking for "1080p or the best you have" means.
    """
    usable = [e for e in entries
              if _yt_kind(e) == "video" and e.get("url")
              and (not cap or (e.get("height") or 0) <= cap)]
    if not usable:
        return {}
    usable.sort(key=lambda e: (
        e.get("height") or 0,
        1 if (_yt_is_mp4(e) if prefer_h264 else not _yt_is_mp4(e)) else 0,
        e.get("bitrate") or 0,
    ), reverse=True)
    return usable[0]


def _yt_pick_audio(entries: list, want_mp4: bool) -> dict:
    """The best audio stream, in the container that will merge cleanly."""
    usable = [e for e in entries if _yt_kind(e) == "audio" and e.get("url")]
    if not usable:
        return {}
    usable.sort(key=lambda e: (
        1 if _yt_is_mp4(e) == want_mp4 else 0,
        e.get("bitrate") or 0,
    ), reverse=True)
    return usable[0]


def _yt_details(data: dict, video_id: str) -> dict:
    details = data.get("videoDetails") or {}
    thumbs = ((details.get("thumbnail") or {}).get("thumbnails")) or []
    best = ""
    if thumbs:
        best = max(thumbs, key=lambda t: t.get("width") or 0).get("url", "")
    try:
        seconds = int(details.get("lengthSeconds") or 0)
    except (TypeError, ValueError):
        seconds = 0
    return {
        "title": (details.get("title") or f"YouTube {video_id}").strip()[:120],
        "uploader": details.get("author") or "",
        "thumbnail": best if _address_ok(best, "youtube") else "",
        "duration": seconds,
    }


def _youtube(url: str, quality: str = "", prefer_h264: bool = True,
             can_merge: bool = True) -> dict:
    """
    Riplox's own route to one YouTube video.

    With ffmpeg present this returns two addresses - a video stream and an
    audio one - because that is the only way YouTube offers anything above
    360p. Without it there is a single muxed stream and it is 360p, which the
    caller is told in as many words rather than left to wonder about.
    """
    video_id = _yt_id(url)

    # Without a merger the muxed stream is the only usable answer, and only
    # ANDROID still carries one - so that is the client worth asking first.
    order = ("IOS", "ANDROID") if can_merge else ("ANDROID", "IOS")

    data = {}
    answered = ""
    refusal = ""
    trouble = ""
    for client in order:
        try:
            answer = _yt_ask(video_id, client)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            trouble = str(exc)
            continue

        status = answer.get("playabilityStatus") or {}
        if status.get("status") == "OK" and answer.get("streamingData"):
            data, answered = answer, client
            break
        # Kept, but not returned yet: the other client may still be allowed
        # to play it, and a refusal from one is not a verdict from YouTube.
        refusal = refusal or _yt_refusal(status)

    if not data:
        if refusal:
            raise DoorError(refusal)
        raise DoorError("YouTube did not answer Riplox's own route"
                        + (f" - {trouble}" if trouble else "") + ".")

    streaming = data.get("streamingData") or {}
    adaptive = streaming.get("adaptiveFormats") or []
    muxed = streaming.get("formats") or []
    info = _yt_details(data, video_id)
    cap = 0 if quality in ("", "best", "mp3") else int(quality)
    note = ""

    chosen = {}
    audio = {}
    if can_merge:
        chosen = _yt_pick_video(adaptive, cap, prefer_h264)
        audio = _yt_pick_audio(adaptive, want_mp4=_yt_is_mp4(chosen))
    if quality == "mp3":
        # Audio only: there is nothing to merge and nothing to pick a height
        # for. The engine's own converter turns it into mp3 afterwards.
        chosen, audio = _yt_pick_audio(adaptive, want_mp4=True), {}

    if not chosen or (can_merge and quality != "mp3" and not audio):
        # Falling back to the muxed stream is a real downgrade, so it is said
        # out loud rather than quietly handed over as if it were the best on
        # offer. Silence here is the failure this whole module exists to stop.
        chosen = next((e for e in sorted(
            muxed, key=lambda e: e.get("height") or 0, reverse=True)
            if e.get("url")), {})
        audio = {}
        if chosen:
            note = (f"Only YouTube's combined {chosen.get('qualityLabel') or 'low'} "
                    f"stream was available on this route"
                    + ("" if can_merge else ", because ffmpeg is not installed "
                                            "and the higher ones arrive as "
                                            "separate video and audio") + ".")

    if not chosen:
        # The shape the SABR switch would take, so it is named here rather
        # than arriving as an empty list nobody can interpret.
        raise DoorError(
            "YouTube offered no directly fetchable stream for that video. "
            "This is what it looks like when YouTube moves a client onto its "
            "own streaming protocol, which Riplox's route does not speak.")

    result = {
        "url": _checked(chosen.get("url", ""), "youtube"),
        "id": video_id,
        "ext": "m4a" if quality == "mp3" else "mp4",
        # The user agent of the client that was actually answered, not a
        # browser's. These addresses are issued to a particular app and the
        # CDN checks that the fetch looks like it came from the same one.
        "headers": {
            "User-Agent": _YT_CLIENTS[answered]["ua"],
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "site": "YouTube",
        "note": note,
    }
    if audio:
        result["audio_url"] = _checked(audio.get("url", ""), "youtube")
    result.update(info)
    return result


# --------------------------------------------------------------------------
# The one entry point
# --------------------------------------------------------------------------

_DOORS = {"tiktok": _tiktok, "instagram": _instagram, "facebook": _facebook,
          "youtube": _youtube}


def resolve(url: str, quality: str = "", prefer_h264: bool = True,
            can_merge: bool = True) -> dict:
    """
    Work out the direct address for a link this module handles.

    Returns the details, or raises DoorError with something worth showing.
    Callers should treat any other exception as "this door did not work" and
    keep yt-dlp's own error, which is the one the user was already told.

    The three extra arguments are only meaningful where a site offers a choice
    of streams, which today is YouTube alone - the other doors are handed one
    file and take it. They are optional so that every existing caller, and any
    test written against the old shape, keeps working unchanged.

    A returned "audio_url" means the video arrives as two streams and the
    caller has to merge them; "note" is a sentence to show the user when what
    came back is not the best the site has.
    """
    door = _DOORS.get(site_of(url))
    if door is None:
        raise DoorError("Riplox has no direct route for that site.")
    # Before anything is fetched, not after: the whole point is that no
    # request leaves by a route the user did not agree to.
    trouble = proxy_problem()
    if trouble:
        raise DoorError(trouble)
    if door is _youtube:
        return _youtube(url, quality=quality, prefer_h264=prefer_h264,
                        can_merge=can_merge)
    return door(url)
