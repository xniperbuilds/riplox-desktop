/*
 * The popup asks the background worker to do the sending rather than doing it
 * itself: a popup is closed the moment it loses focus, and the tab that
 * carries the handoff would go with it.
 */

const pageEl = document.getElementById("page");
const sendEl = document.getElementById("send");
const saidEl = document.getElementById("said");
const stateEl = document.getElementById("state");
const badgeEl = document.getElementById("showBadge");
const inPageEl = document.getElementById("inPageButton");
const permNoteEl = document.getElementById("permNote");
const getEl = document.getElementById("get");
const backEl = document.getElementById("back");

// The same pattern the background registers the script for. One place, so the
// two cannot drift into asking for one thing and injecting into another.
const BUTTON_ORIGINS = ["*://*/*"];

let current = "";

function usable(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url) && url.length <= 2000;
}

function say(text, good) {
  saidEl.textContent = text;
  saidEl.className = "said " + (good ? "ok" : "bad");
  saidEl.hidden = false;
}

/* ------------------------------------------------------------------ state
 *
 * The one thing a popup like this could never say was which of two things had
 * happened: Riplox took the link and is working on it, or Riplox is closed and
 * the link is sitting in a file waiting for it. Both are fine - the second has
 * always worked - but not knowing which is which is the loudest complaint
 * about every tool built this way.
 *
 * It is worked out from the inbox: Riplox empties that file every 1.5 seconds
 * while it runs, so a link still sitting there means nothing is emptying it.
 * Nothing here asks Riplox anything it cannot answer, and nothing can claim to
 * be fresh while being stale.
 */

// Comfortably longer than the 1.5s drain, so a slow moment is never called
// "closed". Being late with the truth beats being early with a guess.
const NOT_COLLECTING = 30;

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* The oldest Riplox this extension can talk to.
 *
 * Bumping this is how a future version of the extension refuses to pretend it
 * works against a Riplox that predates whatever it needs. It is a floor, not a
 * handshake: the extension never asks Riplox to change, it only says plainly
 * which Riplox it is built for. */
const MIN_RIPLOX = "1.5.0";

/** a.b.c compared as numbers, so "1.10.0" is newer than "1.9.0". */
function olderThan(version, floor) {
  const a = String(version || "").split(".").map((n) => parseInt(n, 10) || 0);
  const b = floor.split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const x = a[i] || 0;
    const y = b[i] || 0;
    if (x !== y) return x < y;
  }
  return false;
}

function describe(status) {
  if (status && !status.ok && status.noAnswer) {
    // Nothing is known yet, so nothing is claimed. Opening the popup again is
    // the whole fix, and it usually is by the time it is read.
    return ["Could not check on Riplox just now — open this again in a moment.",
            "warn"];
  }
  if (status && !status.ok && status.reason === "stale") {
    /* Chrome refused the connection because this extension's id is not in the
     * helper's allowed_origins. In practice there is one reason for that: the
     * Riplox on this machine predates this extension. Naming the version is
     * both true and something a person can act on - where the old message,
     * "Riplox is not installed here", sent them off to reinstall an
     * application that was already running. */
    return [`This extension needs Riplox ${MIN_RIPLOX} or newer. The Riplox on `
            + "this PC is older, so it does not recognise the extension yet.",
            "warn"];
  }
  if (status && status.ok && olderThan(status.version, MIN_RIPLOX)) {
    /* It answered, so Riplox is here and working - it is simply older than
     * this extension was built for.
     *
     * The version can be missing rather than low, and that is the ordinary
     * case rather than an edge one: every Riplox built before this field
     * existed answers without it. Probing the installed copy printed
     * "Riplox  is installed" with the hole where the number should be, so an
     * unknown version now says it is unknown instead of pretending to a
     * number nobody sent. */
    return [status.version
              ? `Riplox ${status.version} is installed, and this extension `
                + `needs ${MIN_RIPLOX} or newer.`
              : `An older Riplox is installed. This extension needs `
                + `${MIN_RIPLOX} or newer.`,
            "warn"];
  }
  if (!status || !status.ok) {
    // Its own state, and not "Riplox is closed": the extension is fine and
    // Riplox's helper is not answering. Folding the two together would send
    // somebody off to open an app that is already open.
    return ["Riplox is not installed on this PC, or its helper was never "
            + "registered.", "bad"];
  }
  if (status.waiting > 0 && status.oldest > NOT_COLLECTING) {
    return [`Riplox is not open — ${count(status.waiting, "link is", "links are")}`
            + " waiting for it.", "warn"];
  }
  if (status.active > 0) {
    return [`Riplox is working on ${status.active}.`, "ok"];
  }
  if (status.waiting > 0) {
    return [`${count(status.waiting, "link", "links")} just handed over.`, "ok"];
  }
  // Nothing waiting and nothing running looks identical whether Riplox is open
  // or closed, so this claims neither.
  return ["Anything you send goes to Riplox, or waits for it if it is closed.",
          "plain"];
}

async function showState() {
  let status = null;
  try {
    status = await chrome.runtime.sendMessage({ kind: "status" });
  } catch (e) {
    /* Our own service worker did not answer - it is starting, or the extension
     * is mid-update. That is not the same as Riplox being absent, and saying
     * "Riplox is not installed" here blames the wrong program for a fault
     * inside this one. */
    status = { ok: false, noAnswer: true };
  }
  const [text, tone] = describe(status);
  stateEl.textContent = text;
  stateEl.className = "state " + tone;
  stateEl.hidden = false;

  /* The way out, and only where it is the answer.
   *
   * Shown when the helper is genuinely absent - not when it is merely refusing
   * this extension, because in that case Riplox is already installed and
   * pointing at a download page would be the wrong instruction dressed up as
   * help. Sending somebody to fetch what they already have is how a tool
   * teaches people to ignore it. */
  const missing = !!status && !status.ok && !status.noAnswer
    && status.reason !== "stale";
  const outdated = (!!status && !status.ok && status.reason === "stale")
    || (!!status && status.ok && olderThan(status.version, MIN_RIPLOX));
  getEl.textContent = missing ? "Get Riplox for Windows \u2192"
                              : "Update Riplox \u2192";
  getEl.hidden = !(missing || outdated);
}

/** The host of the tab being looked at, or "" when there is not one. */
function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch (e) {
    return "";
  }
}

/* Putting the button back.
 *
 * The cross on the in-page button hides it on that site for good, and until
 * now that was the end of it: the site went into a list nothing could take it
 * out of, so one mis-click cost that site the button permanently. A dismissal
 * that cannot be undone is not a setting, it is damage.
 *
 * The offer only appears on a site that has actually been dismissed, so the
 * popup stays quiet everywhere else.
 */
async function fillDismissed() {
  const host = hostOf(current);
  const { neverSites } = await chrome.storage.sync.get({ neverSites: [] });
  const list = Array.isArray(neverSites) ? neverSites : [];
  if (!host || !list.includes(host)) {
    backEl.hidden = true;
    return;
  }
  backEl.textContent = `Show the button on ${host} again`;
  backEl.hidden = false;
}

backEl.addEventListener("click", async () => {
  const host = hostOf(current);
  if (!host) return;
  const { neverSites } = await chrome.storage.sync.get({ neverSites: [] });
  const list = (Array.isArray(neverSites) ? neverSites : [])
    .filter((h) => h !== host);
  await chrome.storage.sync.set({ neverSites: list });
  backEl.hidden = true;
  // The content script read that list when the page loaded, so the page has to
  // be loaded again for it to see the change. Saying so beats leaving somebody
  // staring at a button that has not come back yet.
  say(`It will be back on ${host} when you reload the page.`, true);
});

async function fill() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  current = tab?.url || "";
  pageEl.title = current;

  if (!usable(current)) {
    // A settings page, a PDF viewer, a new tab. Say which, rather than letting
    // the button fail after the click.
    pageEl.textContent = current ? "This page cannot be sent." : "No page open.";
    pageEl.classList.add("blocked");
    sendEl.disabled = true;
    return;
  }
  pageEl.textContent = current;
}

async function fillOptions() {
  const saved = await chrome.storage.sync.get({
    showBadge: true, inPageButton: false,
  });
  badgeEl.checked = !!saved.showBadge;
  inPageEl.checked = !!saved.inPageButton;
}

badgeEl.addEventListener("change", async () => {
  await chrome.storage.sync.set({ showBadge: badgeEl.checked });
  chrome.runtime.sendMessage({ kind: "sync" });
});

/* The permission is asked for at the moment it is turned on, and given back
 * the moment it is turned off. If the browser says no, the tick goes back —
 * a switch left on while the thing it controls cannot run is a lie. */
inPageEl.addEventListener("change", async () => {
  if (inPageEl.checked) {
    const granted = await chrome.permissions.request({ origins: BUTTON_ORIGINS })
      .catch(() => false);
    if (!granted) {
      inPageEl.checked = false;
      permNoteEl.textContent = "The browser did not grant access, so the "
        + "button stays off.";
      return;
    }
  } else {
    await chrome.permissions.remove({ origins: BUTTON_ORIGINS }).catch(() => {});
  }
  await chrome.storage.sync.set({ inPageButton: inPageEl.checked });
  chrome.runtime.sendMessage({ kind: "sync" });
  permNoteEl.textContent = inPageEl.checked
    ? "A button now appears on pages. Drag it anywhere, or dismiss it on a "
      + "site with its cross. Turning this off gives the access back."
    : "Turning this on asks the browser for access to pages. Turning it off "
      + "gives that access back. The button can be dragged anywhere, and "
      + "dismissed per site.";
});

sendEl.addEventListener("click", async () => {
  sendEl.disabled = true;
  const answer = await chrome.runtime.sendMessage({ kind: "send", url: current });

  if (answer?.ok) {
    if (answer.via === "scheme") {
      // The quiet route was not available, so a tab has opened with a question
      // in it. Saying so is the difference between a tab that makes sense and
      // one that looks like a bug.
      say("Allow Riplox in the tab that opened.", true);
    } else {
      // "Sent", not "downloading". Riplox does the rest, and a popup that
      // claimed the download had started would be guessing.
      say("Sent to Riplox.", true);
      await showState();
      setTimeout(() => window.close(), 1400);
    }
  } else {
    say(answer?.error || "Could not send it.", false);
    sendEl.disabled = false;
  }
});

/* What the last right-click send said.
 *
 * A send from the context menu has no popup open to talk to, so on its own it
 * can only flash a tick on the icon. This reports the words once, the next
 * time the popup opens - and drops anything stale, because old news presented
 * as current is worse than none.
 */
const NOTE_LIFE = 8000;

async function showLastNote() {
  const { lastNote } = await chrome.storage.session.get({ lastNote: null });
  if (!lastNote || !lastNote.text) return;
  await chrome.storage.session.remove("lastNote");
  if (Date.now() - (lastNote.at || 0) > NOTE_LIFE) return;
  say(lastNote.text, true);
}

fill().then(fillDismissed);
fillOptions();
showState();
showLastNote();
