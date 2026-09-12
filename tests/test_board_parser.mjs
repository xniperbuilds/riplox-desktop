/*
 * The Board's parser, which is the Board's entire security model.
 *
 * Every other control on that feature - the rate limits, the reports, the kill
 * switch - assumes that a stored link points where it says it does. That is
 * true only because the Worker never stores what anybody typed: it matches a
 * platform and a content id and BUILDS a fresh URL out of them. So the thing
 * worth testing is not "was this refused" but "is the rebuilt string exactly
 * the one expected, character for character".
 *
 * Runs on plain Node, no wrangler and no network: parse() is imported straight
 * out of the Worker.
 *
 * The second half is the part that makes the first half worth anything. A test
 * suite that cannot fail is not evidence, so this breaks the parser six ways
 * on purpose and checks that the corpus above notices each one. A mutation
 * that survives means the corpus has a hole, not that the code is fine.
 */
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = join(here, "..", "board", "worker.js");

async function load(text, tag) {
  const shim = join(here, "..", "board", `.under-test-${tag}-${process.pid}.mjs`);
  writeFileSync(shim, text);
  try {
    return await import(pathToFileURL(shim).href);
  } finally {
    unlinkSync(shim);
  }
}

const original = readFileSync(source, "utf8");
const { parse, cleanTitle } = await load(original, "clean");

let passed = 0;
const failures = [];
function check(name, ok, detail = "") {
  if (ok) { passed++; console.log("  PASS  " + name); }
  else { failures.push(name); console.log("  FAIL  " + name + (detail ? " | " + detail : "")); }
}

const YT = "dQw4w9WgXcQ";
const TT = "7300000000000000000";
const XID = "1500000000000000000";
const SNAP = "W7_EDlXWTBiXAEEniNoMPwAAYc5JzY2dnbHR0";

/* ------------------------------------------------------------------ *
 * Links that must be accepted, and the exact string each becomes.
 *
 * The right-hand side is the point. "It was accepted" would still pass if the
 * parser handed back whatever it was given.
 * ------------------------------------------------------------------ */
const ACCEPT = [
  ["youtube, plain", `https://www.youtube.com/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtube, no www", `https://youtube.com/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtube, mobile", `https://m.youtube.com/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtube, music", `https://music.youtube.com/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtu.be short form", `https://youtu.be/${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtube shorts", `https://www.youtube.com/shorts/${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtube live", `https://www.youtube.com/live/${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["youtube embed", `https://www.youtube.com/embed/${YT}`, `https://www.youtube.com/watch?v=${YT}`],

  // Everything a real paste carries and nothing on the board should keep.
  ["tracking parameter dropped", `https://youtu.be/${YT}?si=Xk9_tracking`, `https://www.youtube.com/watch?v=${YT}`],
  ["timestamp dropped", `https://www.youtube.com/watch?v=${YT}&t=42s`, `https://www.youtube.com/watch?v=${YT}`],
  ["playlist context dropped", `https://www.youtube.com/watch?v=${YT}&list=PLabc&index=3`, `https://www.youtube.com/watch?v=${YT}`],
  ["fragment dropped", `https://www.youtube.com/watch?v=${YT}#t=1m`, `https://www.youtube.com/watch?v=${YT}`],
  ["uppercase host", `HTTPS://WWW.YOUTUBE.COM/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["surrounding whitespace", `   https://youtu.be/${YT}\n`, `https://www.youtube.com/watch?v=${YT}`],
  ["http upgraded to https", `http://www.youtube.com/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
  ["trailing slash", `https://www.youtube.com/shorts/${YT}/`, `https://www.youtube.com/watch?v=${YT}`],

  ["tiktok", `https://www.tiktok.com/@some.one/video/${TT}`, `https://www.tiktok.com/@some.one/video/${TT}`],
  ["tiktok, query dropped", `https://www.tiktok.com/@some.one/video/${TT}?is_from_webapp=1`, `https://www.tiktok.com/@some.one/video/${TT}`],

  ["instagram post", "https://www.instagram.com/p/Cabc123XY/", "https://www.instagram.com/p/Cabc123XY/"],
  ["instagram reel becomes a post", "https://www.instagram.com/reel/Cabc123XY/", "https://www.instagram.com/p/Cabc123XY/"],
  ["instagram reels plural", "https://www.instagram.com/reels/Cabc123XY/", "https://www.instagram.com/p/Cabc123XY/"],
  ["instagram tv", "https://www.instagram.com/tv/Cabc123XY/", "https://www.instagram.com/p/Cabc123XY/"],
  ["instagr.am", "https://instagr.am/p/Cabc123XY/", "https://www.instagram.com/p/Cabc123XY/"],

  // The username is dropped rather than bounded: x.com/i/status/<id> reaches
  // the same post, so the one attacker-supplied piece never survives at all.
  ["x, username dropped", `https://x.com/someone/status/${XID}`, `https://x.com/i/status/${XID}`],
  ["twitter.com becomes x.com", `https://twitter.com/someone/status/${XID}`, `https://x.com/i/status/${XID}`],
  ["x, a hostile username is thrown away", `https://x.com/..%2F..%2Fevil/status/${XID}`, `https://x.com/i/status/${XID}`],

  ["snapchat spotlight", `https://www.snapchat.com/spotlight/${SNAP}`, `https://www.snapchat.com/spotlight/${SNAP}`],

  /* This one is here rather than in the refusals below, and it was written as
   * a refusal first - the parser disagreed and the parser was right.
   *
   * A zero-width space inside a host looks exactly like the lookalike attack
   * the Cyrillic case is. It is not. IDNA mapping deletes U+200B outright, so
   * "you<zwsp>tube.com" IS youtube.com - to this Worker, to a browser, and to
   * whatever the user's own machine would resolve. There is nothing to be
   * protected from, and the rebuild produces the clean address either way.
   *
   * Kept as a test because "surely that should be refused" is the obvious
   * reading, and the next person to have that thought should find the answer
   * here instead of tightening the parser against a link that was always
   * genuine. */
  ["zero width inside the host is mapped away, not an attack",
    `https://you​tube.com/watch?v=${YT}`, `https://www.youtube.com/watch?v=${YT}`],
];

/* ------------------------------------------------------------------ *
 * Links that must be refused. Each one is a real technique, not a shape
 * nobody would try.
 * ------------------------------------------------------------------ */
const REFUSE = [
  ["userinfo host", `https://youtube.com@evil.com/watch?v=${YT}`],
  ["userinfo with password", `https://youtube.com:pass@evil.com/watch?v=${YT}`],
  ["suffix confusion", `https://www.youtube.com.evil.com/watch?v=${YT}`],
  ["allowed host in the path", `https://evil.com/youtube.com/watch?v=${YT}`],
  ["allowed host as a parameter", `https://evil.com/?u=https://youtube.com/watch?v=${YT}`],
  ["subdomain that is not listed", `https://videos.youtube.com/watch?v=${YT}`],
  ["cyrillic lookalike", `https://youtub\u0435.com/watch?v=${YT}`],
  ["punycode written out", `https://xn--youtub-9ve.com/watch?v=${YT}`],

  // Redirectors that live on a genuinely allowed host. These are why an
  // allowlist that only asks "which site?" is not enough.
  ["youtube's own redirector", "https://www.youtube.com/redirect?q=https://evil.com"],
  ["facebook's redirector", "https://www.facebook.com/l.php?u=https://evil.com"],
  ["instagram's link shim", "https://l.instagram.com/?u=https://evil.com"],

  ["javascript scheme", `javascript:alert(1)//youtube.com/watch?v=${YT}`],
  ["data scheme", "data:text/html,<script>alert(1)</script>"],
  ["file scheme", "file:///C:/Windows/System32/"],
  ["ftp scheme", `ftp://youtube.com/watch?v=${YT}`],
  ["protocol relative", `//www.youtube.com/watch?v=${YT}`],
  ["no scheme at all", `www.youtube.com/watch?v=${YT}`],

  ["id too short", "https://www.youtube.com/watch?v=abc"],
  ["id too long", `https://www.youtube.com/watch?v=${YT}XXXX`],
  ["id with markup", `https://www.youtube.com/watch?v=${YT}<script>`],
  ["id with a slash", "https://www.youtube.com/watch?v=abc/../def"],
  ["watch with no v", "https://www.youtube.com/watch"],
  ["a channel, not a video", "https://www.youtube.com/@somechannel"],
  ["a playlist, not a video", "https://www.youtube.com/playlist?list=PLabcdefghij"],

  ["tiktok short link", "https://vm.tiktok.com/ZMabcdef/"],
  ["tiktok other short link", "https://vt.tiktok.com/ZSabcdef/"],
  ["tiktok username charset", `https://www.tiktok.com/@ev!l/video/${TT}`],
  ["tiktok username too long", `https://www.tiktok.com/@${"a".repeat(40)}/video/${TT}`],
  ["tiktok extra path segment", `https://www.tiktok.com/@some/one/video/${TT}`],
  ["tiktok profile, not a video", "https://www.tiktok.com/@someone"],

  ["instagram with no shortcode", "https://www.instagram.com/p/"],
  ["instagram profile", "https://www.instagram.com/someone/"],
  ["instagram shortcode with markup in it", "https://www.instagram.com/p/ab<script>cd/"],
  ["instagram shortcode too long", `https://www.instagram.com/p/${"a".repeat(40)}/`],
  ["x with a non-numeric id", "https://x.com/someone/status/abc"],
  ["x id with digits and then junk", `https://x.com/someone/status/${XID}abc`],
  ["x profile", "https://x.com/someone"],
  ["tiktok id with digits and then junk", `https://www.tiktok.com/@someone/video/${TT}abc`],

  ["snapchat profile is not spotlight", "https://www.snapchat.com/add/someone"],
  ["snapchat short link", "https://t.snapchat.com/abcdefgh"],
  ["snapchat token too short", "https://www.snapchat.com/spotlight/abc"],

  // Dropped on purpose. Drive is the one that matters: it was asked for and
  // argued out, and a quiet re-entry through the parser is exactly how a
  // decision like that gets undone.
  ["google drive is not on the board", "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view"],
  ["reddit is not on the board", "https://www.reddit.com/r/videos/comments/abc123/title/"],
  ["facebook is not on the board", "https://www.facebook.com/watch?v=123456789"],
  ["vimeo is not on the board", "https://vimeo.com/123456789"],

  ["empty", ""],
  ["only spaces", "     "],
  ["not a string", 12345],
  ["null", null],
  ["undefined", undefined],
  ["an object that looks like a link", { toString: () => `https://youtu.be/${YT}` }],
  ["absurdly long", "https://www.youtube.com/watch?v=" + "a".repeat(4000)],
];

console.log("\n  The rebuild\n");
for (const [name, input, expected] of ACCEPT) {
  const got = parse(input);
  check(name, got !== null && got.url === expected,
    got === null ? "refused" : `built ${got.url}`);
}

console.log("\n  What must never get through\n");
for (const [name, input] of REFUSE) {
  const got = parse(input);
  check(name, got === null, got ? `accepted as ${got.url}` : "");
}

/* ------------------------------------------------------------------ *
 * Titles
 * ------------------------------------------------------------------ */
console.log("\n  Titles\n");
check("a newline cannot become a layout", cleanTitle("one\ntwo") === "one two");
check("tabs and runs collapse", cleanTitle("  a \t\t b  ") === "a b");
check("zero width characters go", cleanTitle("ab\u200bcd") === "ab cd");
check("direction override goes", cleanTitle("safe\u202egnp.exe") === "safe gnp.exe");
check("capped at 120", cleanTitle("x".repeat(400)).length === 120);
check("not a string is empty", cleanTitle(null) === "" && cleanTitle(undefined) === "");
check("a normal title survives", cleanTitle("Rick Astley - Never Gonna Give You Up") ===
  "Rick Astley - Never Gonna Give You Up");

/* ------------------------------------------------------------------ *
 * Mutation check
 *
 * Six ways to break the parser that a reader might not notice in review. Each
 * has to make the corpus above fail. One that survives is a hole in the
 * corpus - the finding is about these tests, not about the Worker.
 * ------------------------------------------------------------------ */
/* ⚠️ Two guards in the parser are deliberately NOT in this list, and finding
 * that out is worth more than the list itself.
 *
 * Removing the userinfo check and removing the punycode check both leave every
 * case in this file behaving exactly as before. They are not doing any work:
 * the URL parser already puts the real host in url.hostname, so
 * "youtube.com@evil.com" arrives as evil.com and "youtub<cyrillic e>.com"
 * arrives as xn--youtub-9ve.com, and neither is in HOSTS. The exact-host
 * allowlist and the rebuild are carrying the whole thing.
 *
 * They stay in the Worker anyway, as belt and braces, with that written beside
 * them - because the day somebody loosens the allowlist to match by suffix
 * instead of exactly, those two guards stop being redundant and start being
 * the only thing left. Mutating them here would only ever report a hole in
 * this file that cannot be filled, which is worse than saying so plainly.
 */
const MUTATIONS = [
  ["instagram shortcode regex unanchored",
    "const SHORTCODE = /^[A-Za-z0-9_-]{5,24}$/;",
    "const SHORTCODE = /[A-Za-z0-9_-]{5,24}/;"],
  ["numeric id regex unanchored",
    "const DIGITS = /^[0-9]{5,25}$/;",
    "const DIGITS = /[0-9]{5,25}/;"],
  ["scheme check removed",
    'if (url.protocol !== "https:" && url.protocol !== "http:") return null;',
    "if (false) return null;"],
  ["host list becomes a default",
    "const platform = HOSTS[host];",
    'const platform = HOSTS[host] || "youtube";'],
  ["video id regex unanchored",
    "const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;",
    "const VIDEO_ID = /[A-Za-z0-9_-]{11}/;"],
  ["tiktok username unbounded",
    "const TIKTOK_USER = /^[A-Za-z0-9._]{1,24}$/;",
    "const TIKTOK_USER = /^.{1,24}$/;"],
];

console.log("\n  Mutation check - each of these must be caught\n");
let mutant = 0;
for (const [name, from, to] of MUTATIONS) {
  mutant++;
  if (!original.includes(from)) {
    check("mutation " + name, false, "the line it edits is no longer in worker.js");
    continue;
  }
  const broken = await load(original.replace(from, to), "mut" + mutant);

  let caught = false;
  for (const [, input, expected] of ACCEPT) {
    const got = broken.parse(input);
    if (got === null || got.url !== expected) { caught = true; break; }
  }
  if (!caught) {
    for (const [, input] of REFUSE) {
      if (broken.parse(input) !== null) { caught = true; break; }
    }
  }
  check("mutation " + name, caught, "survived - the corpus does not cover it");
}

console.log("");
console.log(`  ${passed} passed, ${failures.length} failed`);
if (failures.length) {
  for (const name of failures) console.log("    " + name);
  process.exit(1);
}
