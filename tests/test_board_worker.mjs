/*
 * The Board's Worker, run for real.
 *
 * test_board_parser.mjs proves the parser in isolation. This proves the thing
 * around it: that a link accepted by the parser is actually stored, comes back
 * out of the feed, cannot be posted twice, stops being postable after five in
 * an hour, disappears when three different people report it, comes back when
 * it is restored, and stops entirely when the kill switch is thrown.
 *
 * Needs a local Worker, the same way the relay's tests do:
 *
 *     cd board && npx wrangler dev --port 8798 --local
 *
 * Every id is generated fresh per run, because a Durable Object keeps its
 * table between runs and this board refuses a link it already has - a second
 * run reusing the same ids would fail for the wrong reason.
 */
const BASE = "http://127.0.0.1:8798";
const KEY = "local-test-key";      // matches board/.dev.vars

let passed = 0;
const failures = [];
function check(name, ok, detail = "") {
  if (ok) { passed++; console.log("  PASS  " + name); }
  else { failures.push(name); console.log("  FAIL  " + name + (detail ? " | " + detail : "")); }
}

async function call(path, body, opts = {}) {
  const headers = { "content-type": "application/json" };
  // Cloudflare's edge sets this on a proxied route and it cannot be sent in
  // from outside. Here there is no edge, so the test plays that part.
  if (opts.ip) headers["CF-Connecting-IP"] = opts.ip;
  if (opts.key) headers["X-Board-Key"] = opts.key;
  const res = await fetch(BASE + path, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

try {
  await fetch(BASE + "/state");
} catch {
  console.log("  The board Worker is not reachable on " + BASE);
  console.log("  start wrangler first:  cd board && npx wrangler dev --port 8798 --local");
  process.exit(0);
}

/* Fresh ids for this run. YouTube ids are exactly eleven characters of a known
 * alphabet, so they are built to that shape rather than to look plausible. */
const seed = Math.random().toString(36).slice(2, 6);
let n = 0;
const vid = () => (seed + "vid" + String(++n).padStart(4, "0")).slice(0, 11).padEnd(11, "z");
const yt = (id) => "https://www.youtube.com/watch?v=" + id;
/* Install ids are seeded too, and for the same reason as the video ids: the
 * object keeps its counters between runs, so a second run with the same id
 * starts with an hour's posting already spent and the rate-limit test fails
 * having proved nothing. Found by running this twice. */
const who = (name) => name + "-" + seed;

/* ------------------------------------------------------------------ state */

const state = await call("/state");
check("the board says it is on", state.ok === true && state.on === true, JSON.stringify(state));
check("it sends a keepalive interval", typeof state.ping === "number" && state.ping > 0);
check("it names its platforms", Array.isArray(state.platforms) && state.platforms.length === 5);
check("google drive is not one of them", !(state.platforms || []).includes("drive"));

/* ------------------------------------------------------------------ check */

const good = await call("/check", { url: "https://youtu.be/" + vid() + "?si=tracking" });
check("check accepts and rebuilds", good.ok === true && good.url.startsWith("https://www.youtube.com/watch?v="));
check("check drops the tracking parameter", good.ok && !good.url.includes("si="));

const bad = await call("/check", { url: "https://evil.com/watch?v=aaaaaaaaaaa" });
check("check refuses an unknown site", bad.ok === false);
check("and says which sites it takes", /YouTube, TikTok, Instagram, X and Snapchat/.test(bad.error || ""));

const short = await call("/check", { url: "https://vm.tiktok.com/ZMabcdef/" });
check("a short link is told what to do", /paste the full one/.test(short.error || ""));

check("check stores nothing", (await call("/feed")).rows
  .every((r) => r.url !== good.url), "a checked link ended up on the board");

/* ------------------------------------------------------------------ share */

const first = vid();
const shared = await call("/share", { url: yt(first), title: "A first link", install: who("install-a") }, { ip: "10.0.0.1" });
check("a link can be shared", shared.ok === true, JSON.stringify(shared));
check("it comes back with an id", shared.ok && typeof shared.row.id === "number");
check("it is stored rebuilt, not as typed", shared.ok && shared.row.url === yt(first));

const again = await call("/share", { url: "https://youtu.be/" + first, title: "same video", install: who("install-b") }, { ip: "10.0.0.2" });
check("the same video cannot be posted twice, in any shape", again.ok === false);
check("and is told why", /already on the board/.test(again.error || ""));

const refused = await call("/share", { url: "https://drive.google.com/file/d/1AbCdEfGhIjK/view", install: who("install-a") }, { ip: "10.0.0.1" });
check("a link the board does not take is refused", refused.ok === false);

/* The title is the one piece of free text on the board and it does not come
 * from a person - but it is not trusted either. */
const nasty = vid();
const messy = await call("/share", {
  url: yt(nasty),
  title: "line one\nline two‮ and an override " + "x".repeat(300),
  install: who("install-a"),
}, { ip: "10.0.0.1" });
check("a title with a newline in it is flattened", messy.ok && !messy.row.title.includes("\n"));
check("a direction override is stripped", messy.ok && !messy.row.title.includes("‮"));
check("a long title is capped", messy.ok && messy.row.title.length <= 120);

/* ------------------------------------------------------------------- feed */

const feed = await call("/feed");
check("the feed returns what was shared", feed.ok && feed.rows.some((r) => r.url === yt(first)));
check("the feed is newest first", feed.ok && feed.rows.length > 1 &&
  feed.rows[0].id > feed.rows[feed.rows.length - 1].id);

const tik = vid();
await call("/share", { url: "https://www.tiktok.com/@someone/video/7300000000000000001", title: "a tiktok", install: who("install-t") }, { ip: "10.0.0.9" });
const onlyTikTok = await call("/feed?platform=tiktok");
check("the platform filter filters", onlyTikTok.ok && onlyTikTok.rows.length > 0 &&
  onlyTikTok.rows.every((r) => r.platform === "tiktok"));

const unknownPlatform = await call("/feed?platform=drive");
check("an unknown platform is refused rather than ignored", unknownPlatform.ok === false);

/* Search is bounded to a date range on purpose - an unbounded LIKE over a
 * table meant to hold years of links is a scan, and the daily row-read
 * allowance is shared with the relay. */
const loose = await call("/feed?q=first");
check("a search with no month is refused", loose.ok === false);
check("and says what to do about it", /Pick a month/.test(loose.error || ""));

const now = new Date();
const from = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1);
const to = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1) - 1;
const found = await call(`/feed?q=first&from=${from}&to=${to}`);
check("a search inside a month finds a title", found.ok && found.rows.some((r) => r.url === yt(first)));

const wildcard = await call(`/feed?q=%25&from=${from}&to=${to}`);
check("a LIKE wildcard is searched for, not obeyed", wildcard.ok && wildcard.rows.length === 0);

/* ------------------------------------------------------------------ limit */

let refusedAt = 0;
for (let i = 1; i <= 7; i++) {
  const res = await call("/share", { url: yt(vid()), title: "flood " + i, install: who("install-flood") }, { ip: "10.9.9.9" });
  if (!res.ok && !refusedAt) refusedAt = i;
}
check("posting stops after five in an hour", refusedAt === 6, "refused at " + refusedAt);

/* ----------------------------------------------------------------- report */

const target = shared.row.id;
const one = await call("/report", { id: target, install: who("reporter-1") }, { ip: "10.1.1.1" });
check("one report does not hide anything", one.ok === true && one.hidden === false);

const twice = await call("/report", { id: target, install: who("reporter-1") }, { ip: "10.1.1.1" });
check("the same person reporting twice is still one report", twice.ok && twice.hidden === false);

await call("/report", { id: target, install: who("reporter-2") }, { ip: "10.1.1.2" });
const third = await call("/report", { id: target, install: who("reporter-3") }, { ip: "10.1.1.3" });
check("three different people hide it", third.ok === true && third.hidden === true);

const afterReports = await call("/feed");
check("a hidden link leaves the feed", afterReports.ok && !afterReports.rows.some((r) => r.id === target));

/* ------------------------------------------------------------------ admin */

const noKey = await call("/admin/hidden", {});
check("admin refuses with no key", noKey.ok === false);
const wrongKey = await call("/admin/hidden", {}, { key: "not-the-key" });
check("admin refuses with the wrong key", wrongKey.ok === false);

const review = await call("/admin/hidden", {}, { key: KEY });
check("the review list holds the hidden link", review.ok && review.rows.some((r) => r.id === target));
check("the review list shows how many reported it", review.ok &&
  (review.rows.find((r) => r.id === target) || {}).reports === 3);

await call("/admin/restore", { id: target }, { key: KEY });
const restored = await call("/feed");
check("restore puts it back on the board", restored.ok && restored.rows.some((r) => r.id === target));
const emptied = await call("/admin/hidden", {}, { key: KEY });
check("and clears its reports, so three more are needed", emptied.ok &&
  !emptied.rows.some((r) => r.id === target));

/* ------------------------------------------------------------ kill switch */

await call("/admin/flag", { name: "readonly", value: "1" }, { key: KEY });
const closed = await call("/share", { url: yt(vid()), install: who("install-a") }, { ip: "10.0.0.1" });
check("read-only stops new links", closed.ok === false);
const stillReadable = await call("/feed");
check("read-only leaves the board readable", stillReadable.ok && stillReadable.rows.length > 0);
await call("/admin/flag", { name: "readonly", value: "" }, { key: KEY });

await call("/admin/flag", { name: "off", value: "1" }, { key: KEY });
const offState = await call("/state");
check("the kill switch turns the board off", offState.ok && offState.on === false);
const offShare = await call("/share", { url: yt(vid()), install: who("install-a") }, { ip: "10.0.0.1" });
check("and nothing can be posted while it is off", offShare.ok === false);
await call("/admin/flag", { name: "off", value: "" }, { key: KEY });
check("and it can be turned back on", (await call("/state")).on === true);

const badFlag = await call("/admin/flag", { name: "anything", value: "1" }, { key: KEY });
check("an unknown flag is refused", badFlag.ok === false);

console.log("");
console.log(`  ${passed} passed, ${failures.length} failed`);
if (failures.length) {
  for (const name of failures) console.log("    " + name);
  process.exit(1);
}
