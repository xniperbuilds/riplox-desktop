/*
 * The store build.
 *
 * Same idea as the bundled extension and deliberately less of it: it reads the
 * address of the page you are on and hands that address to Riplox, which is
 * already installed on this machine. It never fetches anything, never looks at
 * a page, and holds no access to any site.
 *
 * What it does NOT carry, and why - because the difference is the whole reason
 * this folder exists rather than one build serving both:
 *
 *   - no quality picker. Riplox already has that setting, and an empty quality
 *     is documented on the app's side as "use the same default the window
 *     uses" - so leaving it out gives the user their own setting rather than
 *     overriding it with one chosen in a browser popup.
 *   - no site list. The bundled build names the services it is useful on; a
 *     store listing that ships a list of video sites is describing itself as
 *     something the store does not allow.
 *   - no in-page button, so no content script, no scripting permission and no
 *     host access at all. This build cannot see a page even if it wanted to.
 *
 * The bundled build in ../browser-extension keeps all of it. Both talk to the
 * same native host and write into the same inbox.
 */

const SCHEME = "riplox://add";
const HOST = "com.xniperbuilds.riplox";

const DEFAULTS = { showBadge: true };

// Long enough to answer "Open Riplox?" in the tab the question appears in.
const TAB_LINGER = 12000;

async function settings() {
  return chrome.storage.sync.get(DEFAULTS);
}

/**
 * A link Riplox can be given.
 *
 * Only http(s). A file:// or chrome:// address is not something a downloader
 * has any use for, and offering it would mean an error after the click instead
 * of a disabled button before it.
 */
function usable(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) && url.length <= 2000;
}

/**
 * Hand the address over, by whichever route is open.
 *
 * The native host first: silent, instant, no tab - but it exists only where
 * Riplox's installer registered it. The riplox:// scheme second: it works
 * wherever Riplox is installed and can start it when it is closed, at the cost
 * of a question the browser asks in a tab that opens and closes itself.
 *
 * Which one happened is returned, because they feel different and a tab
 * appearing on its own deserves an explanation.
 */
async function send(url) {
  try {
    // No quality is sent on purpose. app.queue_from_browser treats an empty
    // one as "use the default the window uses", so the user's own setting in
    // Riplox wins - which is the correct owner of that decision.
    const answer = await chrome.runtime.sendNativeMessage(HOST, { url });
    if (answer && answer.ok) return "host";
  } catch (e) {
    // No host registered, or it failed. There is another way in.
  }

  const target = `${SCHEME}?url=${encodeURIComponent(url)}`;
  const tab = await chrome.tabs.create({ url: target, active: false });
  await rememberHandoff(tab.id);
  // The fast path, and only that: a service worker is stopped after thirty
  // seconds of quiet and takes its timers with it. The sweep below is what
  // actually guarantees the tab closes.
  setTimeout(() => closeHandoff(tab.id), TAB_LINGER);
  return "scheme";
}

/* Handoff tabs are written down as they open, and any left behind are closed
 * the next time this worker starts. The age check matters: closing on sight
 * would shut the tab while the browser was still asking the question in it. */

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

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "riplox-page",
      title: "Send this page to Riplox",
      contexts: ["page"],
    });
    chrome.contextMenus.create({
      id: "riplox-link",
      title: "Send this link to Riplox",
      contexts: ["link"],
    });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const id = String(info.menuItemId || "");
  const url = id === "riplox-link" ? info.linkUrl : (info.pageUrl || tab?.url);
  if (!usable(url)) {
    await note("That address cannot be sent.");
    return;
  }
  const via = await send(url);
  await ensureBadgeAlarm(BADGE_BUSY);
  await note(via === "host" ? "Sent to Riplox."
                            : "Allow Riplox in the tab that opened.");
});

/* -------------------------------------------------------------------------
 * The badge
 *
 * Riplox's own count of what it is working on, read from its queue by the
 * host. Nothing here guesses: when the host does not answer, the badge is
 * cleared rather than left showing the last number it saw.
 * ---------------------------------------------------------------------- */

const BADGE_ALARM = "riplox-badge";

/* How often to ask.
 *
 * Every tick starts a fresh copy of the helper: sendNativeMessage runs the
 * host, asks, and the host exits. At half a minute that is a process every
 * thirty seconds for as long as the browser is open - on a machine where
 * Riplox is not installed, forever, for an answer that never changes. So the
 * interval follows the answer, and a send puts it straight back to busy.
 */
const BADGE_BUSY = 0.5;      // Chrome will not honour less than this
const BADGE_IDLE = 2;
const BADGE_ASLEEP = 10;

/**
 * Put the alarm on a footing without disturbing one already on it.
 *
 * `alarms.create` with a name that already exists "will be cancelled and
 * replaced" - schedule and all. A create at the top level of a service worker
 * therefore restarts the countdown on every single event, and a browser in
 * ordinary use never goes half a minute without one. That is how a badge like
 * this goes an entire day without firing once.
 */
async function ensureBadgeAlarm(minutes) {
  const existing = await chrome.alarms.get(BADGE_ALARM).catch(() => null);
  if (existing && Math.abs((existing.periodInMinutes || 0) - minutes) < 0.01) return;
  chrome.alarms.create(BADGE_ALARM, { periodInMinutes: minutes });
}

// The tick that says "sent" owns the badge for a moment, so a refresh landing
// a hair later cannot wipe the only confirmation a right-click send ever gets.
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
    // Not installed here, or not answering. Nothing to say, and no hurry.
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
    await chrome.action.setBadgeBackgroundColor({ color: "#0E7490" });
    await chrome.action.setBadgeText({ text: active ? String(active) : "" });
  }
  await ensureBadgeAlarm(active || waiting ? BADGE_BUSY : BADGE_IDLE);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === BADGE_ALARM) refreshBadge();
});

chrome.runtime.onInstalled.addListener(() => ensureBadgeAlarm(BADGE_IDLE));
chrome.runtime.onStartup.addListener(async () => {
  await ensureBadgeAlarm(BADGE_IDLE);
  await sweepHandoff();
  await refreshBadge();
});

/* -------------------------------------------------------------------------
 * Saying what happened
 *
 * A scheme handoff gives nothing back, so nothing here ever claims a download
 * started. "Sent to Riplox" is the most that is known, and the popup carries
 * the sentence explaining the difference.
 * ---------------------------------------------------------------------- */

async function note(text) {
  const at = Date.now();
  // Read by the popup the next time it opens. A right-click send has no popup
  // to talk to, so without this its only feedback is a tick on the icon that
  // could mean anything.
  await chrome.storage.session.set({ lastNote: { text, at }, tickUntil: at + TICK_MS });
  await chrome.action.setBadgeText({ text: "✓" });
  await chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
  setTimeout(() => refreshBadge(), TICK_MS);
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.kind === "send") {
    (async () => {
      if (!usable(msg.url)) {
        reply({ ok: false, error: "That address cannot be sent." });
        return;
      }
      const via = await send(msg.url);
      await ensureBadgeAlarm(BADGE_BUSY);
      refreshBadge();
      reply({ ok: true, via });
    })();
    return true;                 // the reply is coming later
  }

  if (msg?.kind === "sync") {
    (async () => {
      await refreshBadge();
      reply({ ok: true });
    })();
    return true;
  }

  /* What Riplox is doing with what it has been given.
   *
   * Three states, kept apart on purpose. Riplox took it and is working; Riplox
   * is closed and the link is waiting in a file for it; or the helper is not
   * answering at all, which is neither of those and needs different fixing. */
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
