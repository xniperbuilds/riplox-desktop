/*
 * The whole extension in one idea: it never touches a video.
 *
 * It reads the address of the page you are looking at and hands that address to
 * Riplox, which is already installed on this machine. Everything after that -
 * working out what is on the page, choosing a format, fetching the bytes -
 * happens in Riplox, where it always has.
 *
 * That is not a technicality. An extension that fetched media itself would need
 * permissions this one does not ask for, would break when a site changed, and
 * would be a downloader rather than a way to reach one.
 *
 * The handoff is a riplox:// link. Windows already knows which program owns
 * that scheme, so there is no port to find, no token to keep, and nothing
 * listening on this machine that a web page could reach.
 */

const SCHEME = "riplox://add";

// Kept in step with Riplox's own list. "best" means "let Riplox decide", which
// is what its own default does.
const QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360", "mp3"];

const DEFAULTS = {
  quality: "best",
  askEveryTime: false,
  // Empty means every site, which is what it did before this existed. Names
  // are the same words Riplox itself uses, so a rule here means the same
  // thing on both sides.
  sites: [],
  inPageButton: false,
  showBadge: true,
};

async function settings() {
  const saved = await chrome.storage.sync.get(DEFAULTS);
  if (!QUALITIES.includes(saved.quality)) saved.quality = DEFAULTS.quality;
  if (!Array.isArray(saved.sites)) saved.sites = [];
  return saved;
}

/* -------------------------------------------------------------------------
 * Which sites this is for
 *
 * The same short list Riplox offers, and deliberately not the engine's
 * thousand extractor names: a rule naming something the matcher can never
 * produce would sit there looking set and doing nothing.
 * ---------------------------------------------------------------------- */

const SITE_HOSTS = {
  YouTube: ["youtube.com", "youtu.be"],
  TikTok: ["tiktok.com"],
  Instagram: ["instagram.com"],
  Facebook: ["facebook.com", "fb.watch"],
  X: ["x.com", "twitter.com"],
  Reddit: ["reddit.com"],
  Vimeo: ["vimeo.com"],
  Dailymotion: ["dailymotion.com"],
  Twitch: ["twitch.tv"],
  SoundCloud: ["soundcloud.com"],
  Pinterest: ["pinterest.com"],
  Snapchat: ["snapchat.com"],
  LinkedIn: ["linkedin.com"],
};

function siteOf(url) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch (e) {
    return "";
  }
  for (const [name, hosts] of Object.entries(SITE_HOSTS)) {
    if (hosts.some((h) => host === h || host.endsWith("." + h))) return name;
  }
  return "";
}

/** Empty list means everywhere - the same rule the app uses. */
function allowed(url, sites) {
  if (!sites || !sites.length) return true;
  return sites.includes(siteOf(url));
}

/**
 * A link Riplox can be given.
 *
 * Only http(s) is offered. A file:// or chrome:// address is not something a
 * downloader has any use for, and offering it would mean an error later instead
 * of a greyed-out button now.
 */
function usable(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) && url.length <= 2000;
}

// Riplox's native messaging host, named in a file the installer wrote and the
// browser read at startup. This is the quiet route: the browser was told about
// this program in advance, so it does not ask.
const HOST = "com.xniperbuilds.riplox";

// Long enough to answer "Open Riplox?" in the tab the question appears in.
// A shorter wait closed the tab with the question still on it, which is a send
// that silently does nothing.
const TAB_LINGER = 12000;

/**
 * Hand the address over, by whichever route is open.
 *
 * The host first. It is silent, instant, and needs no tab - but it only exists
 * where Riplox was installed by its installer, so it cannot be assumed.
 *
 * The scheme second. It works wherever Riplox is installed at all, and it can
 * start Riplox when it is closed, which the host cannot. Its cost is a question
 * the browser asks - every time, in current versions, since "always allow" is
 * no longer offered.
 *
 * Returned so the popup can say which one happened, because they feel
 * different and a user who sees a tab appear deserves to know why.
 */
async function send(url, quality) {
  try {
    const answer = await chrome.runtime.sendNativeMessage(HOST, { url, quality });
    if (answer && answer.ok) return "host";
  } catch (e) {
    // No host registered, or it failed. Either way there is another way in.
  }

  const target = `${SCHEME}?url=${encodeURIComponent(url)}&q=${encodeURIComponent(quality)}`;
  const tab = await chrome.tabs.create({ url: target, active: false });
  await rememberHandoff(tab.id);
  // The fast path. It is only a fast path: a service worker is killed after 30
  // seconds of quiet and takes its timers with it, so this timeout is allowed
  // to be missed and the sweep below is what actually guarantees the close.
  setTimeout(() => closeHandoff(tab.id), TAB_LINGER);
  return "scheme";
}

/* -------------------------------------------------------------------------
 * The handoff tabs, and why they are written down
 *
 * The tab carrying the riplox:// question closes itself twelve seconds later.
 * That was a setTimeout in the service worker - and a service worker is stopped
 * after thirty seconds of quiet, timers included. When that happened the tab
 * was simply left open, on a page nobody asked for, and the only clue was a
 * stray tab appearing "sometimes".
 *
 * So each one is written down as it opens, and any that are older than the
 * linger get closed the next time this worker starts. The age matters: sweeping
 * on sight would close the tab while the browser was still asking the question
 * in it, which is the same bug wearing the other coat.
 * ---------------------------------------------------------------------- */

async function rememberHandoff(id) {
  if (!id) return;
  const { handoff = [] } = await chrome.storage.session.get({ handoff: [] });
  handoff.push({ id, at: Date.now() });
  await chrome.storage.session.set({ handoff: handoff.slice(-20) });
}

async function closeHandoff(id) {
  await chrome.tabs.remove(id).catch(() => {});
  const { handoff = [] } = await chrome.storage.session.get({ handoff: [] });
  await chrome.storage.session.set({ handoff: handoff.filter((t) => t.id !== id) });
}

async function sweepHandoff() {
  const { handoff = [] } = await chrome.storage.session.get({ handoff: [] });
  if (!handoff.length) return;
  const now = Date.now();
  const keep = [];
  for (const tab of handoff) {
    if (now - tab.at < TAB_LINGER) { keep.push(tab); continue; }
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
  await chrome.storage.session.set({ handoff: keep });
}

/* -------------------------------------------------------------------------
 * The right-click menu
 * ---------------------------------------------------------------------- */

/**
 * Does this address point at a list rather than one video?
 *
 * By shape, not by site: a "list" parameter or a /playlist path are used the
 * same way across the web, and a table of site names here would go stale the
 * first time one of them changed its addresses.
 *
 * Riplox already downloads a whole playlist from exactly this address - it has
 * since before the extension existed. Nothing new happens because of this
 * function; it only lets the menu SAY so, which is the difference between
 * somebody using that and never knowing it was there.
 */
function looksLikeList(url) {
  try {
    const at = new URL(url);
    if (at.searchParams.has("list")) return true;
    return /\/playlist(\/|$)/i.test(at.pathname);
  } catch (e) {
    return false;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "riplox-page",
      title: "Send this page to Riplox",
      contexts: ["page", "video", "audio"],
    });
    chrome.contextMenus.create({
      id: "riplox-link",
      title: "Send this link to Riplox",
      contexts: ["link"],
    });
    /* Audio-only, without opening the popup to change the quality and then
     * having to change it back. Riplox has always accepted this; it was simply
     * unreachable from a right-click. */
    chrome.contextMenus.create({
      id: "riplox-page-mp3",
      title: "Send this page to Riplox as MP3",
      contexts: ["page", "video", "audio"],
    });
    chrome.contextMenus.create({
      id: "riplox-link-mp3",
      title: "Send this link to Riplox as MP3",
      contexts: ["link"],
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const id = String(info.menuItemId || "");
  // A right-click on a link means the link; anywhere else means the page.
  const url = id.startsWith("riplox-link") ? info.linkUrl : (info.pageUrl || tab?.url);
  if (!usable(url)) {
    await note("That address cannot be downloaded.");
    return;
  }
  const { quality, sites } = await settings();
  if (!allowed(url, sites)) {
    // Refused out loud. A filter that silently does nothing is the same as a
    // broken button, and the user has no way to tell which it was.
    await note("Not one of your chosen sites.");
    return;
  }
  // The MP3 entries say what they will do, so they do that rather than
  // whatever the popup was last set to.
  const wanted = id.endsWith("-mp3") ? "mp3" : quality;
  const via = await send(url, wanted);

  // Naming the playlist matters: "sent" for one video and "sent" for ninety
  // read the same, and only one of them is worth going to look at.
  const what = looksLikeList(url) ? "Playlist sent to Riplox."
             : wanted === "mp3" ? "Sent to Riplox as MP3."
             : "Sent to Riplox.";
  await note(via === "host" ? what : "Allow Riplox in the tab that opened.");
});

/* -------------------------------------------------------------------------
 * The in-page button, only if it was asked for
 *
 * Registered at runtime rather than declared in the manifest, so the
 * extension installs with no access to any page and gains it only when
 * somebody turns this on and the browser agrees. Turning it off gives the
 * access back.
 * ---------------------------------------------------------------------- */

const SCRIPT_ID = "riplox-in-page";

/**
 * Where the in-page button may be injected at all.
 *
 * This used to be "every site" no matter what the site list said, because the
 * list was only consulted when somebody pressed the button. So a person who
 * had chosen one site still got the button everywhere, and only learned it was
 * never going to work after pressing it. The rule belongs here, before
 * anything is injected - not there, after the fact.
 */
function buttonMatches(sites) {
  if (!sites || !sites.length) return ["*://*/*"];
  const patterns = [];
  for (const name of sites) {
    for (const host of SITE_HOSTS[name] || []) {
      patterns.push(`*://${host}/*`, `*://*.${host}/*`);
    }
  }
  // A list naming nothing this knows would otherwise register an empty set,
  // which Chrome rejects - leaving no button and no reason why, which is the
  // exact failure this change exists to remove.
  return patterns.length ? patterns : ["*://*/*"];
}

async function syncInPageButton() {
  const { inPageButton, sites } = await settings();
  // Against what the button actually needs, not against "*://*/*". Somebody
  // who granted three sites has granted enough, and testing for everything
  // would call that "not granted" and silently do nothing.
  const granted = await chrome.permissions.contains({ origins: buttonMatches(sites) })
    .catch(() => false);
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] })
    .catch(() => []);

  if (inPageButton && granted) {
    const matches = buttonMatches(sites);
    const unchanged = existing.length
      && JSON.stringify((existing[0].matches || []).slice().sort())
         === JSON.stringify(matches.slice().sort());
    if (unchanged) return;
    // Registering over an existing id fails, so the old one goes first. The
    // site list can change at any moment and the injection has to follow it.
    if (existing.length) {
      await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] })
        .catch(() => {});
    }
    await chrome.scripting.registerContentScripts([{
      id: SCRIPT_ID,
      js: ["content.js"],
      matches,
      runAt: "document_idle",
    }]).catch(() => {});

    // ⚠️ Registering only reaches pages loaded from now on. Somebody turning
    // this on is looking at a page RIGHT NOW, and nothing appearing on it is
    // indistinguishable from the feature being broken - which is exactly how
    // it was first reported. So the tabs already open get it too.
    //
    // content.js guards on window.__riploxButton, so a tab that somehow gets
    // it twice still ends up with one button.
    const already = await chrome.tabs.query({ url: matches }).catch(() => []);
    for (const tab of already) {
      if (!tab.id) continue;
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"],
      }).catch(() => {});      // chrome:// and the store cannot be scripted
    }
  } else if (existing.length) {
    await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] })
      .catch(() => {});
  }
}

/* The site list is edited in the popup, which is a different context. Without
 * this the changed list would not reach the injection until the browser was
 * restarted: the setting would look applied and quietly not be. */
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  if (changes.sites || changes.inPageButton) syncInPageButton();
});

chrome.runtime.onStartup.addListener(syncInPageButton);
chrome.runtime.onInstalled.addListener(syncInPageButton);
chrome.permissions.onRemoved.addListener(syncInPageButton);

/* -------------------------------------------------------------------------
 * The badge
 *
 * Shows how many downloads Riplox has on the go. Asked of Riplox's own host,
 * which reads its queue file - so this is Riplox's count, not a guess made
 * here. When Riplox is not running there is nothing to say, and the badge is
 * cleared rather than left showing the last number it saw.
 * ---------------------------------------------------------------------- */

const BADGE_ALARM = "riplox-badge";

/* How often to ask.
 *
 * Every tick starts a fresh copy of Riplox's helper program: sendNativeMessage
 * runs the host, asks, and the host exits. At half a minute that is a process
 * every thirty seconds for as long as the browser is open - on a machine where
 * Riplox is not even installed, forever, for an answer that never changes.
 *
 * So the interval follows the answer. Busy while something is running, slower
 * when Riplox is idle, slowest when its helper does not answer at all - and
 * straight back to busy the moment something is sent.
 */
const BADGE_BUSY = 0.5;      // Chrome will not honour less than this
const BADGE_IDLE = 2;
const BADGE_ASLEEP = 10;

/**
 * Put the alarm on a footing, without disturbing one already on it.
 *
 * This is the whole reason the badge used to stop: `alarms.create` with a name
 * that already exists "will be cancelled and replaced", schedule and all, and
 * the call sat at the top level of this worker - which runs again on every
 * single event. Any click within the half minute restarted the countdown, so a
 * browser in ordinary use could go all day without the alarm ever firing once.
 */
async function ensureBadgeAlarm(minutes) {
  const existing = await chrome.alarms.get(BADGE_ALARM).catch(() => null);
  if (existing && Math.abs((existing.periodInMinutes || 0) - minutes) < 0.01) return;
  chrome.alarms.create(BADGE_ALARM, { periodInMinutes: minutes });
}

// The tick that says "sent" owns the badge for a moment. Without this the next
// refresh - which can land at any time - wipes it, and the only confirmation a
// right-click send ever had disappears before it is read.
const TICK_MS = 2500;

async function refreshBadge() {
  const { showBadge } = await settings();
  if (!showBadge) {
    await chrome.action.setBadgeText({ text: "" });
    await chrome.alarms.clear(BADGE_ALARM).catch(() => {});
    return;
  }

  let answer = null;
  try {
    const reply = await chrome.runtime.sendNativeMessage(HOST, { ask: "status" });
    if (reply && reply.ok) answer = reply;
  } catch (e) {
    // No host registered, or it failed. Nothing to say, and no hurry to ask again.
  }

  const { tickUntil = 0 } = await chrome.storage.session.get({ tickUntil: 0 });
  const speaking = Date.now() < tickUntil;

  if (!answer) {
    if (!speaking) await chrome.action.setBadgeText({ text: "" });
    await ensureBadgeAlarm(BADGE_ASLEEP);
    return;
  }

  const active = Number(answer.active) || 0;
  const waiting = Number(answer.waiting) || 0;
  if (!speaking) {
    await chrome.action.setBadgeBackgroundColor({ color: "#0e7490" });
    await chrome.action.setBadgeText({ text: active ? String(active) : "" });
  }
  await ensureBadgeAlarm(active || waiting ? BADGE_BUSY : BADGE_IDLE);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === BADGE_ALARM) refreshBadge();
});

// Created where the documentation says to create it, and checked on every start
// in case it was never made or has been cleared.
chrome.runtime.onInstalled.addListener(() => ensureBadgeAlarm(BADGE_IDLE));
chrome.runtime.onStartup.addListener(async () => {
  await ensureBadgeAlarm(BADGE_IDLE);
  await sweepHandoff();
  await refreshBadge();
});

/* -------------------------------------------------------------------------
 * Saying what happened
 *
 * The browser cannot tell us whether Riplox took the link - a scheme handoff
 * gives nothing back. So the badge says "handed over", never "downloaded", and
 * the popup carries the sentence that explains the difference. Claiming more
 * than is known is the failure this is written to avoid.
 * ---------------------------------------------------------------------- */

async function note(text) {
  const at = Date.now();
  // Read by the popup, which is the only place the words can actually be shown.
  // For a long time this was written and never read anywhere, so a right-click
  // send said nothing at all beyond a tick that could mean anything.
  await chrome.storage.session.set({
    lastNote: { text, at },
    tickUntil: at + TICK_MS,
  });
  await chrome.action.setBadgeText({ text: "✓" });
  await chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
  setTimeout(() => refreshBadge(), TICK_MS);
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.kind === "send") {
    (async () => {
      if (!usable(msg.url)) {
        reply({ ok: false, error: "That address cannot be downloaded." });
        return;
      }
      const saved = await settings();
      if (!allowed(msg.url, saved.sites)) {
        reply({ ok: false, error: "Not one of your chosen sites." });
        return;
      }
      // The in-page button carries no quality of its own; the saved one is
      // the same choice the toolbar would have used.
      const via = await send(msg.url, msg.quality || saved.quality);
      await ensureBadgeAlarm(BADGE_BUSY);
      refreshBadge();
      reply({ ok: true, via });
    })();
    return true; // the reply is coming later
  }

  if (msg?.kind === "sync") {
    (async () => {
      await syncInPageButton();
      await refreshBadge();
      reply({ ok: true });
    })();
    return true;
  }

  if (msg?.kind === "sites") {
    reply({ ok: true, names: Object.keys(SITE_HOSTS) });
    return false;
  }

  /* What the popup must ask the browser for. Worked out here because this is
   * where the site list is turned into match patterns, and two copies of that
   * rule would drift the first time either changed. */
  if (msg?.kind === "origins") {
    (async () => {
      const { sites } = await settings();
      reply({ ok: true, origins: buttonMatches(sites) });
    })();
    return true;
  }

  /* What Riplox is doing with what it has been given.
   *
   * The one thing this extension could never say was which of two things had
   * happened: Riplox took the link and is downloading it, or Riplox is closed
   * and the link is sitting in a file waiting for it. Both are fine - the
   * second one has always worked - but not knowing which is which is the
   * loudest complaint about every tool of this shape.
   *
   * A host that does not answer means the host is not installed or is broken.
   * That is a third state and it is reported as itself rather than being
   * folded into "not running", because the two need different fixing. */
  if (msg?.kind === "status") {
    (async () => {
      try {
        const answer = await chrome.runtime.sendNativeMessage(HOST, { ask: "status" });
        if (!answer || !answer.ok) throw new Error("no answer");
        reply({
          ok: true,
          active: Number(answer.active) || 0,
          waiting: Number(answer.waiting) || 0,
          oldest: Number(answer.oldest) || 0,
        });
      } catch (e) {
        reply({ ok: false, noHost: true });
      }
    })();
    return true;
  }
  return false;
});
