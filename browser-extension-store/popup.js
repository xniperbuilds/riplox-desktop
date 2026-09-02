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

function describe(status) {
  if (!status || !status.ok) {
    // Its own state, and not "Riplox is closed": the extension is fine and
    // Riplox's helper is not answering. Folding the two together would send
    // somebody off to open an app that is already open.
    return ["Riplox is not installed here, or its helper is not answering.", "bad"];
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
  const saved = await chrome.storage.sync.get({ showBadge: true });
  badgeEl.checked = !!saved.showBadge;
}

badgeEl.addEventListener("change", async () => {
  await chrome.storage.sync.set({ showBadge: badgeEl.checked });
  chrome.runtime.sendMessage({ kind: "sync" });
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

fill();
fillOptions();
showState();
showLastNote();
