/*
 * The popup asks the background worker to do the sending rather than doing it
 * itself: a popup is closed the moment it loses focus, and the tab that carries
 * the handoff would go with it.
 */

const QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360", "mp3"];

const pageEl = document.getElementById("page");
const qualityEl = document.getElementById("quality");
const sendEl = document.getElementById("send");
const saidEl = document.getElementById("said");

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
 * The one thing this popup could never say was which of two things had
 * happened: Riplox took the link and is downloading it, or Riplox is closed
 * and the link is sitting in a file waiting for it. Both are fine - the second
 * has always worked - but not knowing which is which is the loudest complaint
 * about every tool built this way.
 *
 * It is worked out from the inbox: Riplox empties that file every 1.5 seconds
 * while it runs, so a link still sitting there means nothing is emptying it.
 * Nothing here asks Riplox anything, and nothing can claim to be fresh while
 * being stale.
 */

const stateEl = document.getElementById("state");

// Comfortably longer than the 1.5s drain, so a slow moment is never called
// "closed". Being late with the truth beats being early with a guess.
const NOT_COLLECTING = 30;

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function describe(status) {
  if (status && !status.ok && status.reason === "stale") {
    // Riplox is here. It simply predates this extension, and its helper only
    // answers extensions it has been told about.
    return ["Riplox is installed but has not been told about this extension "
            + "yet. Update Riplox and it will connect.", "warn"];
  }
  if (!status || !status.ok) {
    // A third state, and its own thing: the extension is fine, Riplox's helper
    // is not answering. Folding it into "not running" would send somebody off
    // to open an app that is already open.
    return ["Riplox is not installed here, or its helper is not answering.", "bad"];
  }
  if (status.waiting > 0 && status.oldest > NOT_COLLECTING) {
    return [`Riplox is not open — ${count(status.waiting, "link is", "links are")}`
            + " waiting for it.", "warn"];
  }
  if (status.active > 0) {
    return [`Riplox is downloading ${status.active}.`, "ok"];
  }
  if (status.waiting > 0) {
    return [`${count(status.waiting, "link", "links")} just handed over.`, "ok"];
  }
  // Nothing waiting and nothing running looks identical whether Riplox is open
  // or closed, so this claims neither. Saying "ready" would be a guess, and a
  // guess is the thing the rest of this is written to avoid.
  return ["Anything you send goes to Riplox, or waits for it if it is closed.",
          "plain"];
}

async function showState() {
  let status = null;
  try {
    status = await chrome.runtime.sendMessage({ kind: "status" });
  } catch (e) {
    status = null;
  }
  const [text, tone] = describe(status);
  stateEl.textContent = text;
  stateEl.className = "state " + tone;
  stateEl.hidden = false;
}

async function fill() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  current = tab?.url || "";

  // The full address in the tooltip, two lines of it on screen.
  pageEl.title = current;

  if (!usable(current)) {
    // A settings page, a PDF viewer, a new tab. Say which, rather than letting
    // the button fail after the click.
    pageEl.textContent = current
      ? "This page cannot be downloaded."
      : "No page open.";
    pageEl.classList.add("blocked");
    sendEl.disabled = true;
    return;
  }

  pageEl.textContent = current;

  const saved = await chrome.storage.sync.get({ quality: "best" });
  qualityEl.value = QUALITIES.includes(saved.quality) ? saved.quality : "best";
}

// Remembered as soon as it is changed, so the next send starts where this one
// left off without a Save button nobody would press.
qualityEl.addEventListener("change", () => {
  chrome.storage.sync.set({ quality: qualityEl.value });
});

/* ---------------------------------------------------------------- options */

const badgeEl = document.getElementById("showBadge");
const inPageEl = document.getElementById("inPageButton");
const permNoteEl = document.getElementById("permNote");
const sitesEl = document.getElementById("sites");

async function fillOptions() {
  const saved = await chrome.storage.sync.get({
    showBadge: true, inPageButton: false, sites: [],
  });
  badgeEl.checked = !!saved.showBadge;
  inPageEl.checked = !!saved.inPageButton;

  const answer = await chrome.runtime.sendMessage({ kind: "sites" });
  const names = (answer && answer.names) || [];
  const chosen = new Set(Array.isArray(saved.sites) ? saved.sites : []);

  sitesEl.textContent = "";
  names.forEach((name) => {
    const label = document.createElement("label");
    label.className = "site";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = chosen.has(name);
    box.addEventListener("change", async () => {
      box.checked ? chosen.add(name) : chosen.delete(name);
      await chrome.storage.sync.set({ sites: [...chosen] });
      // A site added while the in-page button is on is a site the browser has
      // not been asked about yet. Without this the tick stays on and the button
      // simply never appears there - the exact "looks set, does nothing" the
      // rest of this file is written to avoid. The click is the user gesture
      // the request needs, so it has to happen here.
      if (inPageEl.checked) {
        const origins = await wantedOrigins();
        const have = await chrome.permissions.contains({ origins })
          .catch(() => false);
        if (!have) {
          const granted = await chrome.permissions.request({ origins })
            .catch(() => false);
          if (!granted) {
            permNoteEl.textContent = "The browser did not grant access to that "
              + "site, so the button will not appear on it.";
          }
        }
      }
      chrome.runtime.sendMessage({ kind: "sync" });
    });
    const text = document.createElement("span");
    text.textContent = name;
    label.append(box, text);
    sitesEl.appendChild(label);
  });
}

badgeEl.addEventListener("change", async () => {
  await chrome.storage.sync.set({ showBadge: badgeEl.checked });
  chrome.runtime.sendMessage({ kind: "sync" });
});

/* The permission is asked for at the moment it is turned on, and given back
   the moment it is turned off. If the browser says no, the tick goes back -
   a switch left on while the thing it controls cannot run is a lie. */
/* What to ask the browser for.
 *
 * This used to be the all-sites pattern whatever the site list said - the
 * widest request
 * Chrome has, and the one whose wording is "Read and change all your data on
 * all websites". Somebody who had ticked YouTube alone was being asked for the
 * entire web, which is both more than the feature needs and the single most
 * common reason a switch like this never gets turned on.
 *
 * With no sites ticked the answer is still every site, because that is what an
 * empty list means everywhere else in this extension.
 */
async function wantedOrigins() {
  const answer = await chrome.runtime.sendMessage({ kind: "origins" })
    .catch(() => null);
  const origins = answer && answer.origins;
  return Array.isArray(origins) && origins.length ? origins : ["*://*/*"];
}

inPageEl.addEventListener("change", async () => {
  if (inPageEl.checked) {
    const origins = await wantedOrigins();
    const granted = await chrome.permissions.request({ origins })
      .catch(() => false);
    if (!granted) {
      inPageEl.checked = false;
      permNoteEl.textContent = "The browser did not grant access, so the "
        + "button stays off.";
      return;
    }
  } else {
    const origins = await wantedOrigins();
    await chrome.permissions.remove({ origins }).catch(() => {});
  }
  await chrome.storage.sync.set({ inPageButton: inPageEl.checked });
  chrome.runtime.sendMessage({ kind: "sync" });
  permNoteEl.textContent = inPageEl.checked
    ? "A button now appears on pages. Turning this off gives that access back."
    : "Turning this on asks the browser for access to pages. Turning it off "
      + "gives that access back.";
});

sendEl.addEventListener("click", async () => {
  sendEl.disabled = true;
  const answer = await chrome.runtime.sendMessage({
    kind: "send",
    url: current,
    quality: qualityEl.value,
  });

  if (answer?.ok) {
    if (answer.via === "scheme") {
      // The quiet route was not available, so a tab has opened with a question
      // in it. Saying so is the difference between a tab that makes sense and
      // one that looks like a bug.
      say("Allow Riplox in the tab that opened.", true);
    } else {
      // "Handed to Riplox", not "downloading". Riplox does the rest, and a
      // popup that claimed the download had started would be guessing.
      say("Handed to Riplox.", true);
      // And say what became of it, rather than closing on a claim nobody can
      // check. The inbox has just changed, so this is the moment it is worth
      // asking again.
      await showState();
      setTimeout(() => window.close(), 1400);
    }
  } else {
    say(answer?.error || "Could not hand it over.", false);
    sendEl.disabled = false;
  }
});

/* What the last right-click send said.
 *
 * A send from the context menu has no popup open to talk to, so all it could
 * ever do was flash a tick on the icon. The words went into session storage and
 * were never read by anything. Now the next opening of the popup reports them,
 * once - stale news is worse than none, so anything older than the tick itself
 * is dropped rather than shown.
 */
const NOTE_LIFE = 8000;

async function showLastNote() {
  const { lastNote } = await chrome.storage.session.get({ lastNote: null });
  if (!lastNote || !lastNote.text) return;
  await chrome.storage.session.remove("lastNote");
  if (Date.now() - (lastNote.at || 0) > NOTE_LIFE) return;
  say(lastNote.text, true);
}

fill();
fillOptions();
showState();
showLastNote();
