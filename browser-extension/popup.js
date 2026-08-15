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
inPageEl.addEventListener("change", async () => {
  if (inPageEl.checked) {
    const granted = await chrome.permissions.request({ origins: ["*://*/*"] })
      .catch(() => false);
    if (!granted) {
      inPageEl.checked = false;
      permNoteEl.textContent = "The browser did not grant access, so the "
        + "button stays off.";
      return;
    }
  } else {
    await chrome.permissions.remove({ origins: ["*://*/*"] }).catch(() => {});
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
      setTimeout(() => window.close(), 900);
    }
  } else {
    say(answer?.error || "Could not hand it over.", false);
    sendEl.disabled = false;
  }
});

fill();
fillOptions();
