"""
What Riplox tells someone when Instagram refuses a post.

This suite exists because of a specific failure, and every case here is one
sentence of that story. The message used to say the refusal was "the post
rather than anything on this end" whenever a few markers lined up. It was
wrong: the cause was a setting on the asking account, and the person reading
that sentence was the only one who could have fixed it. They spent three days
signing in again instead.

So two things are under test. That the advice given matches the signal that
was actually seen - and that nothing is claimed about what Riplox tried unless
the job really recorded doing it.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-verdict-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + (" | " + detail if detail else ""))


DOOR = ("Instagram returned the page but no video in it. That usually means "
        "the account is private, the post was removed, or Instagram wants a "
        "signed-in account for it.")

# The exact shape Instagram answers with for a gated post, measured on four
# real reels: 400, and both markers present.
AUDIENCE = ("ERROR: unable to download video data: HTTP Error 400: This "
            "content isn't available to everyone: it may be inappropriate, "
            "it's unavailable for certain audiences")

print("\n-- the account gate is named, and so is the way out ----------------")
said = engine._door_verdict(AUDIENCE, DOOR, tried_signed_in=True)
check("it says the block is on this account, not on everyone",
      "your account" in said and "rather than from everyone" in said)
check("it names the setting that actually fixes it",
      "Sensitive Content Control" in said)
check("it warns that a birthday alone is not age verification",
      "age-verified" in said and "birthday" in said)
check("it offers the other way out too", "account that can already see" in said)

print("\n-- a challenged login is not an audience gate ----------------------")
# These two look like the case above from a distance, and the sensitive-content
# advice is useless for both - so they have to be told apart from it.
checkpoint = ("ERROR: unable to download video data: HTTP Error 400: "
              "checkpoint_required")
said = engine._door_verdict(checkpoint, DOOR, tried_signed_in=True)
check("it says the sign-in is being challenged, not the post",
      "challenging the sign-in" in said)
check("it does not send them to the content setting",
      "Sensitive Content Control" not in said)
check("it says the post itself is fine", "Nothing about the post" in said)

print("\n-- a regional block is not an audience gate ------------------------")
geo = ("ERROR: Instagram sent an empty media response: this video is not "
       "available in your country")
said = engine._door_verdict(geo, DOOR, tried_signed_in=True)
check("it names the region as the cause", "part of the world" in said)
check("it does not send them to the content setting",
      "Sensitive Content Control" not in said)
check("it says plainly that no setting fixes it", "No setting" in said)
check("and it points at the one thing that could", "proxy" in said)

print("\n-- private and removed stay honestly undecided ---------------------")
# doors.py says Instagram gives no signal that separates these from outside.
# Guessing between them here would undo the point of the whole function.
said = engine._door_verdict("ERROR: Instagram sent an empty media response",
                            DOOR, tried_signed_in=True)
check("both possibilities are offered rather than one being picked",
      "private" in said and "removed" in said)

print("\n-- the sentence that cost three days is gone -----------------------")
for label, engine_err, signed_in in (
        ("audience gate", AUDIENCE, True),
        ("audience gate, signed out", AUDIENCE, False),
        ("empty response", "ERROR: Instagram sent an empty media response", True),
        ("plain 400", "ERROR: HTTP Error 400", False)):
    out = engine._door_verdict(engine_err, DOOR, tried_signed_in=signed_in)
    check("no longer blames the post outright: " + label,
          "rather than anything on this end" not in out)

print("\n-- what it claims to have tried is what the job recorded -----------")
walled = "ERROR: Instagram sent an empty media response"

said_in = engine._door_verdict(walled, DOOR, tried_signed_in=True)
check("having signed in, it says so",
      "signed in" in said_in and "no Instagram sign-in saved" not in said_in)

said_out = engine._door_verdict(walled, DOOR, tried_signed_in=False)
check("having never signed in, it does not pretend it did",
      "no Instagram sign-in saved" in said_out)
check("and it asks for the sign-in that might actually help",
      "Sign in under Settings" in said_out)

print("\n-- it stays out of the way where it has nothing to add -------------")
other = "Riplox has no direct route for that site."
check("a door error it knows nothing about is passed straight through",
      engine._door_verdict("ERROR: HTTP Error 400", other) == other)
check("a door error without the page-but-no-video shape is untouched",
      engine._door_verdict(AUDIENCE, other) == other)
check("an ordinary engine failure is untouched",
      engine._door_verdict("ERROR: Video unavailable", DOOR) == DOOR)

print("\n-- a signed-in attempt stays on the record -------------------------")
# The bug this guards: the signed-out retry runs a second attempt without
# cookies, and that attempt used to overwrite both the flag and the log - so a
# job that really was tried signed in ended up looking as though it never was,
# which is what made the old message unprovable and the diagnosis wrong.
real_open, real_close = engine.open_cookies, engine.close_cookies
engine.open_cookies = lambda settings, url: (SANDBOX / "cookies.txt", False, 1)
engine.close_cookies = lambda path, temp: None


class Attempts(engine.DownloadManager):
    def _spawn(self, job, settings, client, cookie_path):
        job.status = "error"
        job.error = "ERROR: unable to download video data: HTTP Error 400"
        job.log = "yt-dlp --cookies ..." if cookie_path else "yt-dlp (no cookies)"
        return False


try:
    settings = dict(engine.DEFAULT_SETTINGS, download_dir=str(SANDBOX))
    man = Attempts()
    job = engine.Job(url="https://www.instagram.com/reel/AAA/", title="t")

    man._attempt(job, settings, "", with_cookies=True)
    check("a signed-in attempt is recorded", job.tried_signed_in is True)
    check("and so is the state of that attempt", job.sent_cookies is True)

    man._attempt(job, settings, "", with_cookies=False)
    check("the signed-out attempt updates the per-attempt flag",
          job.sent_cookies is False)
    check("but the job still remembers it was tried signed in",
          job.tried_signed_in is True)

    # The whole point: the message that gets built afterwards must be able to
    # say "signed in" truthfully, even though the last attempt was not.
    said = engine._door_verdict("ERROR: Instagram sent an empty media response",
                                DOOR, job.tried_signed_in)
    check("so the message can honestly say it was tried signed in",
          "no Instagram sign-in saved" not in said)

    print("\n-- the signed-in log survives the retry ----------------------------")
    man2 = Attempts()
    job2 = engine.Job(url="https://www.instagram.com/reel/BBB/", title="t")
    man2._attempt(job2, settings, "", with_cookies=True)
    man2._signed_out_retry(job2, settings)
    check("the record still shows the session that was sent",
          "--cookies" in job2.log, job2.log[:70])
    check("and the signed-out attempt is in there too",
          "no cookies" in job2.log)
finally:
    engine.open_cookies, engine.close_cookies = real_open, real_close

print("\n-- only the API gets a vote on whether a post is withheld ----------")
# The bug this guards is the one that misled this investigation twice: the
# words below really do appear in Instagram's web bundle, which is served for
# the front page, a profile and a post alike. Searching it for a verdict finds
# one every time, whatever the post actually is.
import doors                                                  # noqa: E402

shell = ("<!DOCTYPE html><html><script>window.__d=function(){"
         + "var a='login_required';var b='certain audiences';"
         + "var c={is_content_restricted:false};" * 40
         + "}</script></html>")
check("the web page never produces a verdict, however many markers it carries",
      doors._ig_walled(shell) is False, "len=%d" % len(shell))

refusal = ('{"message": "This content isn\'t available to everyone: it may be '
           'inappropriate, it\'s unavailable for certain audiences", '
           '"status": "fail"}')
check("a JSON refusal does produce one", doors._ig_walled(refusal) is True)

check("a flag set on the item counts too",
      doors._ig_walled('{"is_content_restricted": true}') is True)
check("and the flag being false does not",
      doors._ig_walled('{"is_content_restricted": false}') is False)

check("an ordinary ok answer is not a refusal",
      doors._ig_walled('{"status": "ok", "items": []}') is False)
check("something that is not JSON at all has no verdict",
      doors._ig_walled("<html>certain audiences</html>") is False)
check("and neither has an empty body", doors._ig_walled("") is False)

print("\n-- the shortcode maths, checked against Instagram's own answer -----")
# Not a formula taken on trust: this is the number Instagram itself returned
# for this shortcode in the same reply that carried the gating ruling below.
check("the id matches the one Instagram returned",
      doors._ig_media_id("DcMirpjxAQB") == "3966697904948904961",
      doors._ig_media_id("DcMirpjxAQB"))
check("the trailing share addressing is dropped",
      doors._ig_media_id("DcMirpjxAQB" + "x" * 28) == "3966697904948904961")
check("a letter that is not in the alphabet gives nothing rather than a wrong id",
      doors._ig_media_id("bad!code") == "")

print("\n-- Instagram's own reason is read from its own field ---------------")
# A real reply, recorded. The point of this route is that the reason does not
# have to be inferred from the shape of a failure - it is stated outright.
GATED = json.dumps({
    "data": {"xig_polaris_media": {
        "__typename": "XIGPolarisVideoMedia",
        "pk": "3966697904948904961", "code": "DcMirpjxAQB",
        "if_not_gated_logged_out": None,
        "gating_ruling": {
            "gating_type": 3,
            "description": "This content is age-restricted based on your age "
                           "or account settings.",
            "title": "Age-restricted content"}}},
    "errors": [{"message": "A server error field_exception occured."}]})

said = doors._ig_gating(GATED)
check("the ruling's title is picked up", "Age-restricted content" in said)
check("and its description too", "age-restricted based on your age" in said)
check("an answer with no ruling says nothing",
      doors._ig_gating('{"data": {"xig_polaris_media": {"code": "x"}}}') == "")
check("neither does a null data block",
      doors._ig_gating('{"data": null}') == "")
check("nor anything that is not JSON", doors._ig_gating("<html></html>") == "")

print("\n-- and it reaches the user as advice, not as a shrug ---------------")
seen3 = []


def gql_gated(opener, code, page=""):
    seen3.append("graphql")
    return GATED


realg = (doors._ig_graphql, doors._ig_page, doors._ig_embed,
         doors._ig_mobile, doors._opener)
doors._ig_graphql = gql_gated
doors._ig_page = doors._ig_embed = doors._ig_mobile = lambda o, c, page="": ""
doors._opener = lambda: None
try:
    try:
        doors._instagram("https://www.instagram.com/reel/CCC/")
        check("it refused", False, "no error raised")
    except doors.DoorError as exc:
        said = str(exc)
        check("the query ran first", seen3 == ["graphql"], str(seen3))
        check("Instagram's own words are quoted back",
              "Age-restricted content" in said)
        check("it says the block is about the account, not the post",
              "about the account asking" in said)
        check("it names the setting", "Sensitive Content Control" in said)
        check("and the age-verification catch is spelled out",
              "age-verified" in said)
finally:
    (doors._ig_graphql, doors._ig_page, doors._ig_embed,
     doors._ig_mobile, doors._opener) = realg

print("\n-- a JSON refusal outranks whatever the pages looked like ----------")
# Routes do not vote equally. Before this, whichever route ran first decided
# the outcome; now an HTML body decides nothing and the API decides everything.
seen = []


def fake_page(opener, code, page=""):
    # Mirrors the real one: the caller fetches this page once and hands it
    # back in, so a second ask must not become a second request.
    if page:
        return page
    seen.append("page")
    return shell


def fake_embed(opener, code, page=""):
    seen.append("embed")
    return shell


def fake_mobile(opener, code, page=""):
    seen.append("mobile")
    return refusal


def fake_gql_quiet(opener, code, page=""):
    seen.append("graphql")
    return ""                       # the query is out; the rest must still run


real = (doors._ig_graphql, doors._ig_page, doors._ig_embed,
        doors._ig_mobile, doors._opener)
doors._ig_graphql = fake_gql_quiet
doors._ig_page, doors._ig_embed = fake_page, fake_embed
doors._ig_mobile, doors._opener = fake_mobile, lambda: None
try:
    try:
        doors._instagram("https://www.instagram.com/reel/AAA/")
        check("it refused rather than inventing an address", False, "no error")
    except doors.DoorError as exc:
        said = str(exc)
        # The page is fetched once, before the loop, because two routes want
        # it. After that the query runs first among the routes. Both facts
        # matter, and this is the case that pins them.
        check("the page was fetched exactly once",
              seen.count("page") == 1, str(seen))
        check("every route was consulted",
              sorted(seen) == ["embed", "graphql", "mobile", "page"], str(seen))
        check("and the query ran before embed and mobile",
              seen.index("graphql") < seen.index("embed") < seen.index("mobile"),
              str(seen))
        check("the API's refusal is what the message reports",
              "withholding this one" in said)
        check("and it names the setting that can fix it",
              "Sensitive Content Control" in said)
finally:
    (doors._ig_graphql, doors._ig_page, doors._ig_embed,
     doors._ig_mobile, doors._opener) = real

print("\n-- the routing rule itself, pinned -------------------------------")
# Mutation testing found this gap: every other case here passes whether or not
# routes are filtered by speaks_json, because the page is not JSON and the
# verdict readers refuse non-JSON anyway. So the rule "only the API votes" was
# never actually under test. This case puts a JSON refusal in the mouth of an
# HTML route - impossible in the wild, which is the point: it fails the moment
# the filter is removed, and passes only while it is there.
seen4 = []


def page_speaking_json(opener, code, page=""):
    if page:
        return page                   # same cache the real one honours
    seen4.append("page")
    return GATED                      # an HTML route must still not be believed


def quiet(opener, code, page=""):
    return ""


real4 = (doors._ig_graphql, doors._ig_page, doors._ig_embed,
         doors._ig_mobile, doors._opener)
doors._ig_graphql = quiet
doors._ig_page = page_speaking_json
doors._ig_embed = doors._ig_mobile = quiet
doors._opener = lambda: None
try:
    try:
        doors._instagram("https://www.instagram.com/reel/DDD/")
        check("it refused", False, "no error raised")
    except doors.DoorError as exc:
        said = str(exc)
        check("the page route was consulted", seen4 == ["page"], str(seen4))
        check("but its verdict was NOT used",
              "Age-restricted content" not in said, said[:60])
        check("so the honest fallback is what the user gets",
              "private" in said and "removed" in said)
finally:
    (doors._ig_graphql, doors._ig_page, doors._ig_embed,
     doors._ig_mobile, doors._opener) = real4

print("\n-- pages alone are not enough to call something restricted ---------")
seen2 = []


def only_pages(opener, code):
    seen2.append(1)
    return shell


real2 = (doors._ig_graphql, doors._ig_page, doors._ig_embed,
         doors._ig_mobile, doors._opener)
# The query is included deliberately: a route that is meant to answer in JSON
# but hands back the page must still produce no verdict at all.
doors._ig_graphql = only_pages
doors._ig_page = doors._ig_embed = doors._ig_mobile = only_pages
doors._opener = lambda: None
try:
    try:
        doors._instagram("https://www.instagram.com/reel/BBB/")
        check("it still refused", False, "no error")
    except doors.DoorError as exc:
        check("with no API verdict it does not claim a restriction",
              "withholding this one" not in str(exc), str(exc)[:60])
        check("it falls back to the honest list of maybes",
              "private" in str(exc) and "removed" in str(exc))
finally:
    (doors._ig_graphql, doors._ig_page, doors._ig_embed,
     doors._ig_mobile, doors._opener) = real2

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
