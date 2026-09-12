/*
 * Riplox Board - one public room where people post links, and nothing else.
 *
 * Separate Worker from the relay on purpose. They share a Cloudflare account
 * and therefore a daily free allowance, but not a code path: the relay is what
 * Send runs on, and nothing written here should ever be a reason to redeploy
 * it. See CHAT_PLAN.md section 4 for why that allowance is the thing to watch
 * rather than the bill.
 *
 * The whole security model is parse(). Read that first.
 */

/* ------------------------------------------------------------------ *
 * The parser
 *
 * This does not inspect a link and decide whether it looks safe. It parses
 * the link into a platform and a content id, and then BUILDS A NEW URL from
 * those two things. What the user typed is never stored and never shown to
 * anybody - it is read for an id and thrown away.
 *
 * That is what makes the allowlist hold. Every attack on a checking allowlist
 * works by finding a string that passes the check and still points somewhere
 * else: youtube.com@evil.com, youtube.com.evil.com, a Cyrillic е in the host,
 * youtube.com/redirect?q=..., a tracking parameter carrying a second URL. A
 * rebuild cannot carry any of them, because the only things that survive it
 * are the pieces matched below, and each of those has a fixed character set.
 *
 * Nothing in here is allowed to use engine.site_of() or anything like it. That
 * function matches any dotted label, so youtube.evil.com returns "YouTube". It
 * labels shelves in the Library; it is not a gate.
 * ------------------------------------------------------------------ */

/* A platform gets on this list only if the engine has an extractor for it.
 * Checked against the engine's own list, not remembered: Snapchat is here as
 * Spotlight alone because SnapchatSpotlight is the only Snapchat extractor it
 * carries, and a story link would sit on the board looking like every other
 * row and fail the moment somebody pressed Download. */
const PLATFORMS = ["youtube", "tiktok", "instagram", "x", "snapchat"];

/* Hosts, exactly. Not suffixes, not substrings - the host has to equal one of
 * these after the leading "www." is removed. "youtube.com.evil.com" is not in
 * this list and never matches it. */
const HOSTS = {
  "youtube.com": "youtube",
  "m.youtube.com": "youtube",
  "music.youtube.com": "youtube",
  "youtu.be": "youtube",
  "tiktok.com": "tiktok",
  "instagram.com": "instagram",
  "instagr.am": "instagram",
  "x.com": "x",
  "twitter.com": "x",
  "mobile.twitter.com": "x",
  "snapchat.com": "snapchat",
  "t.snapchat.com": "snapchat",
};

/* The id shapes. Each is anchored at both ends and each has a bounded length,
 * because a rebuilt URL is only as safe as the pieces it is built from. The
 * TikTok username is the one attacker-supplied piece that has to survive into
 * the rebuilt address - TikTok needs it - so its character set is written down
 * here rather than left to whoever writes the regex on the day. */
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;              // YouTube
const SHORTCODE = /^[A-Za-z0-9_-]{5,24}$/;           // Instagram
const DIGITS = /^[0-9]{5,25}$/;                      // TikTok, X
const TIKTOK_USER = /^[A-Za-z0-9._]{1,24}$/;
const SPOTLIGHT = /^[A-Za-z0-9_-]{20,128}$/;

/* Anything longer than this is not a link somebody meant to share. Checked
 * before parsing so a megabyte of text never reaches a regex. */
const MAX_URL = 2048;
const MAX_TITLE = 120;

/**
 * (platform, ref, url) for a link this board accepts, or null.
 *
 * null is the only refusal. There is deliberately no "nearly" - a caller
 * cannot be handed something half-parsed and decide for itself.
 */
export function parse(raw) {
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text || text.length > MAX_URL) return null;

  /* Only ever https, and only ever parsed by the URL parser rather than by
   * hand. A hand-rolled split is how "//youtube.com/watch?v=x" and
   * "javascript:alert(1)//youtube.com" get through: both contain the right
   * letters in the right order and neither is an https URL. */
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;

  /* Credentials in a URL exist to make the host look like something it is
   * not: https://youtube.com@evil.com/ is a request to evil.com, and reads to
   * a person as YouTube. Nothing legitimate posts one.
   *
   * ⚠️ Belt and braces, and measured as such: the mutation check in
   * tests/test_board_parser.mjs removes this line and every case in that file
   * still behaves identically, because the URL parser has already put evil.com
   * in url.hostname and evil.com is not in HOSTS. It stays because the day
   * somebody matches hosts by suffix instead of exactly, this stops being
   * redundant and starts being the only thing left. */
  if (url.username || url.password) return null;

  /* An internationalised host is normalised to punycode by the URL parser, so
   * a Cyrillic е in "youtubе.com" arrives here as "xn--youtub-9ve.com". Both
   * that and any surviving non-ASCII are refused outright rather than compared
   * against the list - there is no legitimate lookalike.
   *
   * ⚠️ Redundant today for the same reason the line above is: xn--youtub-9ve
   * .com is not in HOSTS either. Kept for the same reason.
   *
   * Note what this does NOT refuse, because it looks like it should: a
   * zero-width space inside a host. IDNA deletes U+200B, so "you<zwsp>tube
   * .com" simply IS youtube.com - here, in a browser, everywhere - and the
   * rebuild hands back the clean address. There was nothing to defend
   * against; that case is in the accepted half of the test file with the
   * reasoning written out. */
  let host = url.hostname.toLowerCase();
  if (/[^a-z0-9.-]/.test(host) || host.includes("xn--")) return null;
  if (host.startsWith("www.")) host = host.slice(4);

  const platform = HOSTS[host];
  if (!platform) return null;

  /* Path segments, empty ones dropped. Every shape below is matched against
   * these rather than against the path string, so a doubled slash or a
   * trailing one cannot change which branch is taken. */
  const seg = url.pathname.split("/").filter(Boolean).map(decodeSafe);
  if (seg.some((s) => s === null)) return null;

  if (platform === "youtube") return youtube(host, seg, url);
  if (platform === "tiktok") return tiktok(seg);
  if (platform === "instagram") return instagram(seg);
  if (platform === "x") return exTwitter(seg);
  if (platform === "snapchat") return snapchat(seg);
  return null;
}

/* A segment that will not decode is not a segment anybody typed. %-escapes are
 * decoded before matching so that "%2e%2e" and friends are compared as the
 * characters they are, not as the escape that hides them. */
function decodeSafe(part) {
  try {
    return decodeURIComponent(part);
  } catch {
    return null;
  }
}

function made(platform, ref, url) {
  return { platform, ref, url };
}

/* YouTube.
 *
 * /redirect is the one that matters here. It lives on youtube.com, it is a
 * genuine YouTube path, and it exists to send a browser somewhere else - so an
 * allowlist that asks only "is this youtube.com?" hands out links to anywhere.
 * It is not refused by name below; it simply is not one of the four shapes
 * that produce an id, and only those four produce anything at all. Naming bad
 * paths would mean keeping a list of them forever. */
function youtube(host, seg, url) {
  const out = (id) => (VIDEO_ID.test(id) ? made("youtube", id, "https://www.youtube.com/watch?v=" + id) : null);

  if (host === "youtu.be") return seg.length === 1 ? out(seg[0]) : null;
  if (seg.length === 1 && seg[0] === "watch") return out(url.searchParams.get("v") || "");
  if (seg.length === 2 && (seg[0] === "shorts" || seg[0] === "live" || seg[0] === "embed")) return out(seg[1]);
  return null;
}

/* TikTok.
 *
 * Short links (vm.tiktok.com, vt.tiktok.com) are refused rather than resolved.
 * Resolving one means this Worker fetching TikTok on every post: a new failure
 * mode, a rate limit that is not ours, and a way to make the Worker fetch a
 * host somebody else chose. The app says "paste the full link" instead. */
function tiktok(seg) {
  if (seg.length !== 3 || seg[1] !== "video") return null;
  const user = seg[0].startsWith("@") ? seg[0].slice(1) : "";
  if (!TIKTOK_USER.test(user) || !DIGITS.test(seg[2])) return null;
  return made("tiktok", seg[2], "https://www.tiktok.com/@" + user + "/video/" + seg[2]);
}

function instagram(seg) {
  if (seg.length < 2) return null;
  if (seg[0] !== "p" && seg[0] !== "reel" && seg[0] !== "reels" && seg[0] !== "tv") return null;
  if (!SHORTCODE.test(seg[1])) return null;
  return made("instagram", seg[1], "https://www.instagram.com/p/" + seg[1] + "/");
}

/* X.
 *
 * The username is dropped rather than copied: x.com/i/status/<id> reaches the
 * same post, so the one attacker-supplied string in this shape does not have
 * to survive into the rebuilt address at all. When a piece can be thrown away
 * instead of bounded, throw it away. */
function exTwitter(seg) {
  if (seg.length < 3 || (seg[1] !== "status" && seg[1] !== "statuses")) return null;
  if (!DIGITS.test(seg[2])) return null;
  return made("x", seg[2], "https://x.com/i/status/" + seg[2]);
}

function snapchat(seg) {
  if (seg.length !== 2 || seg[0] !== "spotlight") return null;
  if (!SPOTLIGHT.test(seg[1])) return null;
  return made("snapchat", seg[1], "https://www.snapchat.com/spotlight/" + seg[1]);
}

/**
 * A title, reduced to something that can only ever be shown as text.
 *
 * The person posting sends this - their own app read it from the platform, and
 * they had already chosen to open that link, so nothing leaks that they had
 * not already done themselves. Everybody else gets a readable row without any
 * reader's address touching a platform.
 *
 * ⚠️ It is not trusted input. Somebody who uploads a video controls its title,
 * so a spammer can upload "cheap followers wa.me/..." and post it. That is
 * what the report path is for. What is enforced here is only that a title can
 * never be anything except a short line of plain text.
 */
export function cleanTitle(raw) {
  if (typeof raw !== "string") return "";
  /* Control characters and line breaks out first: a title is one line, and a
   * newline in a stored string is how a list turns into a layout. Unicode
   * direction marks go too - they reorder text on screen without appearing in
   * it, which is exactly how a link is made to read as something else. */
  let flat = "";
  for (const ch of raw) {
    const c = ch.codePointAt(0);
    const bad = c < 0x20 || c === 0x7f ||          // control characters
      (c >= 0x200b && c <= 0x200f) ||              // zero width, direction marks
      (c >= 0x202a && c <= 0x202e) ||              // embedding and override
      (c >= 0x2066 && c <= 0x2069);                // isolates
    flat += bad ? " " : ch;
  }
  flat = flat
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > MAX_TITLE ? flat.slice(0, MAX_TITLE - 1) + "…" : flat;
}

/* ------------------------------------------------------------------ *
 * Limits
 *
 * Every number here is in one place so that raising one is an edit rather
 * than a search. The posting limits are per install and per address; the
 * reporting limit is there because the report button is itself a thing that
 * can be abused, and three clicks is cheap.
 * ------------------------------------------------------------------ */
const LIMITS = {
  postPerInstallHour: 5,
  postPerInstallDay: 20,
  postPerIpHour: 10,
  postPerIpDay: 40,
  reportPerInstallDay: 10,
  connectPerIpHour: 30,
  /* Reports needed to hide a row from everybody - from this many different
   * installs AND this many different addresses. */
  hideAt: 3,
};

/* How often a connected app should say hello, in seconds.
 *
 * Sent to the client on connect rather than compiled into it. This is the
 * whole cost lever: every hello is an incoming WebSocket message, those are
 * billed twenty to one, and requests are the allowance this runs out of first.
 * Doubling this number halves what the Board costs, on copies that are already
 * installed, without a release. A number hard-coded in the app would mean
 * finding that out and being unable to act on it for a release cycle.
 */
const PING_SECONDS = 600;

/* Admission control.
 *
 * The relay and the Board spend the same daily allowance, and on the free plan
 * going over it does not produce a bill, it produces errors - which would take
 * Send down for everybody. So the Board stops admitting NEW connections at
 * half the daily request allowance and leaves the rest alone. A Board that is
 * full is a bad afternoon; a relay that is down is everyone's Send broken.
 *
 * Counted by connection rather than by message: one row written per connection
 * is affordable, one per hello would spend the row budget to measure the
 * request budget. Each connection is assumed to cost about a day of pings,
 * which is the worst case for a client that opens the Board and leaves it.
 */
const DAY_REQUEST_CEILING = 50000;
const REQUESTS_PER_CONNECTION = Math.ceil(86400 / PING_SECONDS / 20) + 1;

/* How many rows a read may touch. Search is a LIKE, and a LIKE over a table
 * meant to hold years of links is a scan - a few unbounded ones would spend
 * the five million daily row reads that the relay also lives on. So a search
 * is always inside a date range and never reads more than this. */
const PAGE = 60;
const SEARCH_SCAN = 4000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const room = env.BOARD.get(env.BOARD.idFromName("public"));

    /* One room, one object, one name. A board with invented room ids would be
     * a way for anybody to make this account create objects and write rows -
     * the same hole the relay had to close. There is no id in the address
     * here, so there is nothing to invent. */
    return room.fetch(new Request(url, request));
  },
};

export class Board {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.sql = state.storage.sql;

    /* Runs again every time the object wakes from hibernation, which is why
     * nothing below may rely on anything held in memory. */
    state.blockConcurrencyWhile(async () => {
      this.sql.exec(`CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        ref TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        at INTEGER NOT NULL,
        who TEXT NOT NULL,
        reports INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0)`);
      /* Every read this serves is "newest first, optionally one platform,
       * optionally one date range". Both indexes exist so that none of those
       * is ever a scan. */
      this.sql.exec("CREATE INDEX IF NOT EXISTS links_at ON links(at DESC)");
      this.sql.exec("CREATE INDEX IF NOT EXISTS links_platform_at ON links(platform, at DESC)");
      /* Duplicate suppression, for nothing, as a property of the table rather
       * than a rule somebody has to remember to apply. Permanent rather than a
       * thirty-day window because links here are permanent: if it is still on
       * the board, posting it again is still a duplicate. */
      this.sql.exec("CREATE UNIQUE INDEX IF NOT EXISTS links_key ON links(platform, ref)");

      this.sql.exec(`CREATE TABLE IF NOT EXISTS reports (
        link INTEGER NOT NULL,
        who TEXT NOT NULL,
        ip TEXT NOT NULL,
        at INTEGER NOT NULL,
        PRIMARY KEY (link, who))`);

      /* Counters. One row per (what, who, window), so a limit check reads and
       * writes one row and an expired window is one delete rather than a
       * sweep over history. */
      this.sql.exec(`CREATE TABLE IF NOT EXISTS hits (
        k TEXT PRIMARY KEY,
        n INTEGER NOT NULL,
        until INTEGER NOT NULL)`);
    });
  }

  /* ---------------------------------------------------------------- *
   * The socket, and why it hibernates
   *
   * state.acceptWebSocket(), never server.accept(). With accept() this object
   * stays in memory for as long as a socket is open and is billed for every
   * second of it - one always-on PC on that path once ate 83% of a day's
   * allowance on the relay, which is the single most expensive thing anybody
   * has learned about this account. With the hibernation API the app stays
   * connected while this object sleeps.
   * ---------------------------------------------------------------- */

  async webSocketMessage(ws, raw) {
    let msg;
    try {
      msg = JSON.parse(typeof raw === "string" ? raw : "");
    } catch {
      return;
    }
    /* "I am still here." The only thing a client ever sends down the socket:
     * posting is an HTTP request, so the socket carries one message shape and
     * cannot be used to write anything. */
    if (msg.hello) ws.send(JSON.stringify({ ok: true, ping: PING_SECONDS }));
  }

  async webSocketClose() { /* nothing held per-socket, so nothing to undo */ }
  async webSocketError() { /* same */ }

  push(row) {
    for (const ws of this.state.getWebSockets()) {
      try {
        ws.send(JSON.stringify({ row }));
      } catch {
        /* A socket that has gone away is not an error worth failing a post
         * over. The row is in the table; the next reader will see it. */
      }
    }
  }

  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;
    const ip = request.headers.get("CF-Connecting-IP") || "";

    /* The body is read once, here, before anything decides whether to answer.
     *
     * Sending a response while the request body is still unread is an error in
     * Workers, not a tidiness problem: the admin path refused a wrong key
     * without reading its body and the whole connection dropped, so the caller
     * saw "network connection lost" where the answer was a plain "no". Reading
     * it up front means no handler can return early into that. */
    const body = request.method === "POST" ? await body_(request) : {};

    if (path === "/state") return json(await this.state_());
    if (path === "/check" && request.method === "POST") return json(this.check(body));
    if (path === "/ws") return this.open(ip);
    if (path === "/feed") return json(await this.feed(url));
    if (path === "/share" && request.method === "POST") return json(await this.share(body, ip));
    if (path === "/report" && request.method === "POST") return json(await this.report(body, ip));
    if (path.startsWith("/admin/")) {
      return json(await this.admin(request.headers.get("X-Board-Key") || "", path, body));
    }
    return json({ ok: false, error: "No such address." }, 404);
  }

  /* What the app asks before it shows anything. The kill switch lives here:
   * turning the Board off reaches every installed copy without a release, and
   * without anybody having to go back to an older version. */
  async state_() {
    return {
      ok: true,
      on: (await this.flag("off")) !== "1",
      posting: (await this.flag("readonly")) !== "1",
      notice: (await this.flag("notice")) || "",
      ping: PING_SECONDS,
      platforms: PLATFORMS,
    };
  }

  /**
   * "Would you take this link?" - parse only. Writes nothing, stores nothing.
   *
   * This exists so that the app does not need a parser of its own. It could
   * have had one, for instant feedback without a round trip, and that would
   * have been a second copy of the one thing here that must never be wrong -
   * two parsers that agree on the day they are written and quietly stop
   * agreeing three releases later.
   *
   * It also earns the round trip twice over: reading a title costs the app a
   * yt-dlp run of several seconds, and this is what stops that being spent on
   * a link that was never going to be accepted.
   */
  check(body) {
    const link = parse(body.url);
    if (!link) return { ok: false, error: refusal(body.url) };
    return { ok: true, platform: link.platform, url: link.url };
  }

  async open(ip) {
    if ((await this.flag("off")) === "1") {
      return json({ ok: false, error: "The board is closed." }, 503);
    }
    const addr = await this.addr(ip);
    if (addr && !(await this.allow("conn:" + addr, 3600, LIMITS.connectPerIpHour))) {
      return json({ ok: false, error: "Too many connections." }, 429);
    }
    /* Admission control, described where DAY_REQUEST_CEILING is defined. */
    const today = "day:" + new Date().toISOString().slice(0, 10);
    const used = await this.bump(today, 86400 * 2);
    if (used * REQUESTS_PER_CONNECTION > DAY_REQUEST_CEILING) {
      return json({ ok: false, error: "The board is busy today. Try again tomorrow." }, 503);
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.state.acceptWebSocket(server);
    return new Response(null, { status: 101, webSocket: client });
  }

  /* ---------------------------------------------------------------- *
   * Reading
   * ---------------------------------------------------------------- */

  async feed(url) {
    const platform = url.searchParams.get("platform") || "";
    const before = int(url.searchParams.get("before"), 0);
    const from = int(url.searchParams.get("from"), 0);
    const to = int(url.searchParams.get("to"), 0);
    const q = (url.searchParams.get("q") || "").trim().slice(0, 60);

    if (platform && !PLATFORMS.includes(platform)) {
      return { ok: false, error: "Unknown platform." };
    }

    const where = ["hidden = 0"];
    const args = [];
    if (platform) { where.push("platform = ?"); args.push(platform); }
    if (before) { where.push("id < ?"); args.push(before); }
    if (from) { where.push("at >= ?"); args.push(from); }
    if (to) { where.push("at <= ?"); args.push(to); }

    /* A search is never global.
     *
     * LIKE cannot use an index, so this is a scan of whatever the other
     * conditions left - which is why a search without a date range is refused
     * rather than quietly run over everything. SEARCH_SCAN caps it a second
     * time, because a month can still be large and the row-read allowance is
     * shared with the relay. */
    if (q) {
      if (!from && !to) return { ok: false, error: "Pick a month to search in." };
      where.push("title LIKE ? ESCAPE '\\'");
      args.push("%" + like(q) + "%");
    }

    const limit = q ? SEARCH_SCAN : PAGE + 1;
    const sql = "SELECT id, platform, url, title, at FROM links WHERE " +
      where.join(" AND ") + " ORDER BY id DESC LIMIT ?";
    const rows = [...this.sql.exec(sql, ...args, limit)];

    const page = rows.slice(0, PAGE);
    return { ok: true, rows: page, more: rows.length > page.length };
  }

  /* ---------------------------------------------------------------- *
   * Writing
   * ---------------------------------------------------------------- */

  async share(body, ip) {
    if ((await this.flag("off")) === "1" || (await this.flag("readonly")) === "1") {
      return { ok: false, error: "The board is not taking links right now." };
    }

    const who = await this.hash("who:" + (body.install || ""));
    const addr = await this.addr(ip);

    if (await this.banned(who, addr)) return { ok: false, error: "Blocked." };

    /* The only authority. The app checks the same shapes before it asks, so
     * that a refusal is instant and the wording is the same either way - but
     * that check is for the person typing, not for this. Riplox is open
     * source: anybody can patch the app out of the way and talk to this
     * address directly, so nothing the app says is believed here. */
    const link = parse(body.url);
    if (!link) return { ok: false, error: refusal(body.url) };

    const caps = [
      ["post:" + who, 3600, LIMITS.postPerInstallHour],
      ["postd:" + who, 86400, LIMITS.postPerInstallDay],
    ];
    /* Only when the address is actually known - see addr(). Counting everybody
     * under one empty key would turn "ten an hour each" into "ten an hour for
     * the whole board", which is a far worse failure than not counting. */
    if (addr) {
      caps.push(["postip:" + addr, 3600, LIMITS.postPerIpHour]);
      caps.push(["postipd:" + addr, 86400, LIMITS.postPerIpDay]);
    }

    for (const [k, window, cap] of caps) {
      if (!(await this.allow(k, window, cap))) {
        return { ok: false, error: "You have shared enough links for now. Try again later." };
      }
    }

    const at = Date.now();
    const title = cleanTitle(body.title);
    try {
      this.sql.exec(
        "INSERT INTO links (platform, ref, url, title, at, who) VALUES (?, ?, ?, ?, ?, ?)",
        link.platform, link.ref, link.url, title, at, who);
    } catch {
      /* The unique index did its job. Nothing else can fail this insert: every
       * other column is built above rather than taken from the request. */
      return { ok: false, error: "That link is already on the board." };
    }

    const id = [...this.sql.exec("SELECT last_insert_rowid() AS id")][0].id;
    const row = { id, platform: link.platform, url: link.url, title, at };
    this.push(row);
    return { ok: true, row };
  }

  /* Report.
   *
   * Nothing a user does here deletes anything. Three reports hide a row; the
   * row stays in the table with a flag, and the only place anything is truly
   * removed is the admin path. The person who posted is told nothing - a
   * "your link was reported" message only teaches a spammer how many installs
   * they need.
   */
  async report(body, ip) {
    const id = int(body.id, 0);
    if (!id) return { ok: false, error: "Nothing to report." };

    const who = await this.hash("who:" + (body.install || ""));
    const addr = await this.addr(ip);
    if (!(await this.allow("rep:" + who, 86400, LIMITS.reportPerInstallDay))) {
      return { ok: false, error: "You have reported enough for today." };
    }

    /* The first report hides the row for the person who sent it, and the app
     * does that itself without waiting for an answer. This only records it.
     * That one-click relief is most of why nobody needs to go and find two
     * friends to silence something. */
    this.sql.exec("INSERT OR IGNORE INTO reports (link, who, ip, at) VALUES (?, ?, ?, ?)",
      id, who, addr, Date.now());

    /* Distinct installs AND distinct addresses, counted separately, because an
     * install id is a random value anybody can reset. Unknown addresses are
     * not counted as an address at all - three reports that all arrived with
     * no address are three installs and one blank, and treating that blank as
     * three different people is exactly the thing the second count is for. */
    const seen = [...this.sql.exec(
      "SELECT COUNT(DISTINCT who) AS installs, COUNT(DISTINCT ip) AS addrs " +
      "FROM reports WHERE link = ? AND ip != ''", id)][0] || { installs: 0, addrs: 0 };
    const installs = [...this.sql.exec(
      "SELECT COUNT(DISTINCT who) AS n FROM reports WHERE link = ?", id)][0].n;

    const hide = installs >= LIMITS.hideAt && seen.addrs >= LIMITS.hideAt;
    this.sql.exec("UPDATE links SET reports = ?, hidden = ? WHERE id = ?",
      installs, hide ? 1 : 0, id);
    return { ok: true, hidden: hide };
  }

  /* ---------------------------------------------------------------- *
   * Admin
   *
   * One secret, compared in constant time, and the endpoint refuses everybody
   * when it is unset rather than falling back to a default - the same shape
   * XCipher's announce key ended up with after a guessable default was found
   * in it.
   * ---------------------------------------------------------------- */

  async admin(key, path, body) {
    if (!this.env.BOARD_KEY || !same(key, this.env.BOARD_KEY)) {
      return { ok: false, error: "No." };
    }

    if (path === "/admin/hidden") {
      /* The review list: everything users have hidden, newest first, with the
       * report count, so there is somewhere for Restore to be pressed. */
      return {
        ok: true,
        rows: [...this.sql.exec(
          "SELECT id, platform, url, title, at, reports FROM links WHERE hidden = 1 ORDER BY id DESC LIMIT 200")],
      };
    }
    if (path === "/admin/restore") {
      this.sql.exec("UPDATE links SET hidden = 0 WHERE id = ?", int(body.id, 0));
      this.sql.exec("DELETE FROM reports WHERE link = ?", int(body.id, 0));
      return { ok: true };
    }
    if (path === "/admin/delete") {
      this.sql.exec("DELETE FROM links WHERE id = ?", int(body.id, 0));
      this.sql.exec("DELETE FROM reports WHERE link = ?", int(body.id, 0));
      return { ok: true };
    }
    if (path === "/admin/flag") {
      /* off / readonly / notice. The kill switch, and a way to leave the board
       * readable while posting is closed - which is the softer thing to reach
       * for when something is wrong but not dangerous. */
      const name = String(body.name || "");
      if (!["off", "readonly", "notice"].includes(name)) return { ok: false, error: "No such flag." };
      await this.state.storage.put("flag:" + name, String(body.value || ""));
      return { ok: true };
    }
    if (path === "/admin/ban") {
      await this.state.storage.put("ban:" + String(body.who || body.ip || ""), "1");
      return { ok: true };
    }
    return { ok: false, error: "No such address." };
  }

  /* ---------------------------------------------------------------- *
   * Small things
   * ---------------------------------------------------------------- */

  async flag(name) {
    return (await this.state.storage.get("flag:" + name)) || "";
  }

  async banned(who, ip) {
    if (await this.state.storage.get("ban:" + who)) return true;
    // An unknown address is not a banned one. Without this, banning "" once
    // would ban everybody whose address never arrived.
    return Boolean(ip && (await this.state.storage.get("ban:" + ip)));
  }

  /**
   * One counter, one row. True while there is room left under `cap`.
   *
   * Windows are fixed rather than sliding: a row carries the moment it stops
   * counting, and an expired row is overwritten rather than swept up. That
   * costs one row write per check, which is what the daily allowance of a
   * hundred thousand is comfortably for at this size, and it means no alarm
   * and no background tidying job.
   */
  async allow(key, window, cap) {
    const now = Date.now();
    const row = [...this.sql.exec("SELECT n, until FROM hits WHERE k = ?", key)][0];
    if (!row || row.until < now) {
      this.sql.exec("INSERT OR REPLACE INTO hits (k, n, until) VALUES (?, 1, ?)",
        key, now + window * 1000);
      return true;
    }
    if (row.n >= cap) return false;
    this.sql.exec("UPDATE hits SET n = n + 1 WHERE k = ?", key);
    return true;
  }

  /** Same counter, but returns the new total and never refuses. */
  async bump(key, window) {
    const now = Date.now();
    const row = [...this.sql.exec("SELECT n, until FROM hits WHERE k = ?", key)][0];
    if (!row || row.until < now) {
      this.sql.exec("INSERT OR REPLACE INTO hits (k, n, until) VALUES (?, 1, ?)",
        key, now + window * 1000);
      return 1;
    }
    this.sql.exec("UPDATE hits SET n = n + 1 WHERE k = ?", key);
    return row.n + 1;
  }

  /**
   * A salted hash, and the reason the word "anonymous" survives being read.
   *
   * Raw addresses are never written down. What is stored is this, and the salt
   * is a Worker secret rather than a constant in a public repository - without
   * it, an address is a thirty-two bit space that anybody could walk through
   * to match a hash back to a person.
   */
  /**
   * The caller's address, hashed - or an empty string when there isn't one.
   *
   * CF-Connecting-IP is set by Cloudflare's own edge and cannot be sent in
   * from outside a proxied route, which is what makes it worth counting. But
   * "always set in production" is not "always set", and the failure if it is
   * ever missing had to be chosen rather than stumbled into: hashing an empty
   * string gives one key that every visitor in the world shares, which turns a
   * per-person limit into a limit for the entire board. Empty means unknown,
   * and unknown is not counted.
   */
  async addr(ip) {
    return ip ? await this.hash(ip) : "";
  }

  async hash(value) {
    const salt = this.env.BOARD_SALT || "";
    const bytes = new TextEncoder().encode(salt + " " + value);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].slice(0, 16)
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  }
}

/* Why a link was refused, in the words the person needs.
 *
 * "Not accepted" on its own is the message that makes somebody paste the same
 * link four more times. The two cases worth separating are a short link, which
 * has an action ("paste the full one"), and everything else. */
function refusal(raw) {
  const text = String(raw || "").toLowerCase();
  if (/(vm|vt)\.tiktok\.com|fb\.watch|t\.co\/|bit\.ly|tinyurl/.test(text)) {
    return "That is a short link - open it and paste the full one.";
  }
  return "This board only takes links from YouTube, TikTok, Instagram, X and Snapchat.";
}

/* LIKE has its own wildcards, and a search for "100%" must not become a search
 * for anything starting with "100". */
function like(text) {
  return text.replace(/[\\%_]/g, (c) => "\\" + c);
}

function int(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
}

async function body_(request) {
  try {
    const parsed = JSON.parse(await request.text());
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/* Constant time, so that a wrong key cannot be improved one character at a
 * time by watching how long the answer takes. */
function same(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}
