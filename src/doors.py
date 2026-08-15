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
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
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
    return ""


def handles(url: str) -> bool:
    return bool(site_of(url))


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _opener() -> urllib.request.OpenerDirector:
    """
    A fresh jar per link.

    TikTok answers the first request from an unknown client with a wall page
    and a Set-Cookie, and the second request - carrying that cookie - with the
    real thing. Keeping the jar is the entire trick; a stateless request can
    never get past the first step, which is why so many attempts at this look
    like the site is blocking the machine.
    """
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))


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


def _tiktok_detail(post_id: str) -> tuple:
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
        opener = _opener()                 # a clean jar, every time
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
        raise DoorError("TikTok has age-restricted that post, so it cannot be "
                        "fetched without a signed-in account.")
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
# The one entry point
# --------------------------------------------------------------------------

_DOORS = {"tiktok": _tiktok, "instagram": _instagram, "facebook": _facebook}


def resolve(url: str) -> dict:
    """
    Work out the direct address for a link this module handles.

    Returns the details, or raises DoorError with something worth showing.
    Callers should treat any other exception as "this door did not work" and
    keep yt-dlp's own error, which is the one the user was already told.
    """
    door = _DOORS.get(site_of(url))
    if door is None:
        raise DoorError("Riplox has no direct route for that site.")
    return door(url)
