/*
 * The in-page button.
 *
 * Injected only after someone turns it on and the browser grants access to
 * pages - it is not part of the extension anyone installs. That order matters:
 * the whole design of this extension is that it can see nothing unless asked,
 * and a button on every page is exactly the kind of thing that quietly ends up
 * reading every page.
 *
 * It still never touches a video. It reads the address of the page it is on
 * and hands that to the background worker, same as the toolbar button.
 *
 * It can be dragged. A button pinned to one corner sits on top of whatever the
 * site put there - on YouTube it lands squarely over the suggestion list - and
 * a fixed thing covering the page is a thing people turn off. Where it is put
 * is remembered, so it is moved once and not every time.
 */

(() => {
  if (window.__riploxButton) return;          // one per page, not per frame load
  window.__riploxButton = true;

  const ID = "riplox-send-button";
  if (document.getElementById(ID)) return;

  const PLACE_KEY = "buttonPlace";
  const MARGIN = 8;                 // never let it touch the edge
  // Below this, a press is a click and not a drag. Without it every click
  // moves the button a pixel or two and the page thinks it was dragged.
  const DRAG_SLOP = 4;

  const button = document.createElement("button");
  button.id = ID;
  button.type = "button";
  /* The words live in their own element rather than on the button.
   * The click handler rewrites them to say what happened, and doing that with
   * button.textContent would delete the dismiss cross sitting beside them. */
  /* The mark.
   *
   * Riplox's own arrow, drawn rather than typed. A text arrow is whatever
   * glyph the page's font happens to have and looks like punctuation; this is
   * the same shape as the app icon and the installer, so what lands on the
   * page is recognisably this product instead of the grey pill every extension
   * on the web already puts there.
   *
   * Built with createElementNS and attributes, never innerHTML: sites that
   * enforce Trusted Types - YouTube among them, which is the page this button
   * exists for - refuse innerHTML outright, and the button would simply never
   * appear on the one site that matters most.
   */
  const NS = "http://www.w3.org/2000/svg";

  function stroke(d) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    // Dark on a bright tile, not white on a dim one. Measured: white strokes
    // hit 2.3:1 against the light end of a vivid cyan and vanished; this is
    // 10.8:1 there and 7.1:1 at the dark end.
    path.setAttribute("stroke", "#05202B");
    path.setAttribute("stroke-width", "1.8");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    return path;
  }

  const glyph = document.createElementNS(NS, "svg");
  glyph.setAttribute("viewBox", "0 0 16 16");
  glyph.setAttribute("width", "15");
  glyph.setAttribute("height", "15");
  glyph.setAttribute("aria-hidden", "true");
  glyph.append(stroke("M8 2.6V9.6"), stroke("M4.7 6.6 8 9.9l3.3-3.3"),
               stroke("M3.4 13.1h9.2"));

  const mark = document.createElement("span");
  mark.appendChild(glyph);
  Object.assign(mark.style, {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flex: "0 0 auto",
    width: "24px",
    height: "24px",
    // Follows the body's odd corner, one step tighter. The two shapes reading
    // as one object is the whole reason the silhouette is memorable.
    borderRadius: "8px 8px 8px 3px",
    background: "linear-gradient(160deg, #5BE1F5 0%, #22B8CF 100%)",
    boxShadow: "inset 0 -1px 0 rgba(3,25,35,.22)",
  });
  button.appendChild(mark);

  const label = document.createElement("span");
  label.textContent = "Send to Riplox";
  button.appendChild(label);
  button.setAttribute("aria-label", "Send this page to Riplox. Drag to move it.");
  button.title = "Send this page to Riplox — drag to move";

  // All of it inline: a page's own stylesheet must not be able to make this
  // look like part of the site, and this must not leak style into the page.
  const QUIET = "#9FB3C8";      // ~8:1 on the body. Quiet, not faint.
  const LOUD = "#E8F6F8";       // ~16:1

  /* Why this stays readable on a white article and on a black video frame.
   *
   * It used to be a flat panel at opacity 0.55, and a half-transparent thing
   * has no colour of its own - it becomes whatever is behind it. On a bright
   * page it washed out to unreadable, and there is no value of that number
   * that could have been right for every page on the web.
   *
   * So it is fully opaque, and the separation comes from two rings: a white
   * hairline immediately outside the dark body, and a dark halo outside that.
   * Against black the white ring cuts it out; against white the dark halo
   * does. Neither ring has to know what is behind it - which is the point,
   * because this lands on pages nobody has seen.
   *
   * Being unobtrusive is now the label colour's job rather than the whole
   * control's. Dimming text costs nothing legibility needs; dimming the
   * control costs exactly that.
   *
   * The one square corner is deliberate. Every floating button on the web is
   * an evenly rounded pill, and evenly rounded is the shape that disappears
   * into whatever site it is sitting on. Breaking one corner is the cheapest
   * thing that makes a silhouette somebody recognises a second time.
   */
  Object.assign(button.style, {
    position: "fixed",
    right: "18px",
    bottom: "18px",
    zIndex: "2147483647",
    display: "inline-flex",
    alignItems: "center",
    gap: "8px",
    padding: "5px 12px 5px 5px",
    border: "0",
    borderRadius: "12px 12px 12px 4px",
    background: "linear-gradient(180deg, #16202E 0%, #0B1220 100%)",
    color: QUIET,
    font: "500 13px/1 ui-sans-serif, system-ui, -apple-system, \"Segoe UI\", sans-serif",
    letterSpacing: ".01em",
    cursor: "grab",
    boxShadow: "0 0 0 1px rgba(255,255,255,.92), 0 0 0 3px rgba(3,10,20,.55),"
             + " inset 0 1px 0 rgba(255,255,255,.08),"
             + " 0 10px 24px -12px rgba(0,0,0,.85)",
    opacity: "1",
    transition: "color .15s ease, transform .15s ease",
    touchAction: "none",
    userSelect: "none",
    WebkitFontSmoothing: "antialiased",
  });

  button.addEventListener("mouseenter", () => {
    button.style.color = LOUD;
    button.style.transform = "translateY(-1px)";
  });
  button.addEventListener("mouseleave", () => {
    if (dragging) return;
    button.style.color = QUIET;
    button.style.transform = "none";
  });

  /* ---------------------------------------------------------------- moving */

  /** Put it at a top-left position, kept inside the window. */
  function placeAt(left, top) {
    const box = button.getBoundingClientRect();
    const maxLeft = Math.max(MARGIN, window.innerWidth - box.width - MARGIN);
    const maxTop = Math.max(MARGIN, window.innerHeight - box.height - MARGIN);
    const x = Math.min(Math.max(left, MARGIN), maxLeft);
    const y = Math.min(Math.max(top, MARGIN), maxTop);
    // Switched to left/top once moved: keeping right/bottom as well would
    // stretch the button between two anchors.
    button.style.left = `${Math.round(x)}px`;
    button.style.top = `${Math.round(y)}px`;
    button.style.right = "auto";
    button.style.bottom = "auto";
    return { x: Math.round(x), y: Math.round(y) };
  }

  // Stored as a fraction of the window rather than pixels: a laptop screen and
  // an external monitor are different sizes, and a remembered 1500px would put
  // the button off the edge of the smaller one.
  function remember(x, y) {
    const box = button.getBoundingClientRect();
    const fx = x / Math.max(window.innerWidth - box.width, 1);
    const fy = y / Math.max(window.innerHeight - box.height, 1);
    try {
      chrome.storage.sync.set({
        [PLACE_KEY]: { fx: Math.min(Math.max(fx, 0), 1),
                       fy: Math.min(Math.max(fy, 0), 1) },
      });
    } catch (e) {
      // Storage refused - the button still works, it just starts in the corner.
    }
  }

  function restore() {
    try {
      chrome.storage.sync.get({ [PLACE_KEY]: null }, (saved) => {
        const place = saved && saved[PLACE_KEY];
        if (!place || typeof place.fx !== "number") return;
        const box = button.getBoundingClientRect();
        placeAt(place.fx * (window.innerWidth - box.width),
                place.fy * (window.innerHeight - box.height));
      });
    } catch (e) {
      /* leave it in the corner */
    }
  }

  let dragging = false;
  let moved = false;
  let grabX = 0;
  let grabY = 0;

  button.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const box = button.getBoundingClientRect();
    grabX = event.clientX - box.left;
    grabY = event.clientY - box.top;
    dragging = true;
    moved = false;
    button.setPointerCapture(event.pointerId);
    button.style.cursor = "grabbing";
    button.style.color = LOUD;
    button.style.transition = "none";
  });

  button.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const dx = Math.abs(event.clientX - (button.getBoundingClientRect().left + grabX));
    const dy = Math.abs(event.clientY - (button.getBoundingClientRect().top + grabY));
    if (!moved && dx < DRAG_SLOP && dy < DRAG_SLOP) return;
    moved = true;
    placeAt(event.clientX - grabX, event.clientY - grabY);
  });

  function endDrag(event) {
    if (!dragging) return;
    dragging = false;
    button.style.cursor = "grab";
    button.style.transition = "color .15s ease, transform .15s ease";
    try {
      button.releasePointerCapture(event.pointerId);
    } catch (e) { /* already released */ }
    if (moved) {
      const box = button.getBoundingClientRect();
      remember(box.left, box.top);
    }
  }

  button.addEventListener("pointerup", endDrag);
  button.addEventListener("pointercancel", endDrag);

  // A window that gets smaller must not leave the button outside it.
  window.addEventListener("resize", () => {
    const box = button.getBoundingClientRect();
    if (button.style.left) placeAt(box.left, box.top);
  });

  /* ---------------------------------------------------------------- sending */

  let busy = false;
  button.addEventListener("click", async (event) => {
    // The end of a drag is not a click on the button, whatever the browser
    // calls it. Without this, moving the button also sends the page.
    if (moved) {
      moved = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (busy) return;
    busy = true;
    const original = label.textContent;
    label.textContent = "Sending…";
    try {
      const answer = await chrome.runtime.sendMessage({
        kind: "send", url: location.href,
      });
      // Says what actually happened, and never claims a download: handing the
      // address over is all this can know about.
      label.textContent = !answer ? "Riplox did not answer"
        : answer.ok ? (answer.via === "host" ? "Sent" : "Allow it in the new tab")
        : (answer.error || "Could not send");
    } catch (e) {
      // Two very different things arrive here, and calling both of them
      // "Riplox is not reachable" sends somebody to check an app that is
      // running perfectly. When the extension is reloaded or updated, every
      // content script already on a page is orphaned - its chrome.runtime is
      // dead and chrome.runtime.id is gone. Nothing is wrong with Riplox; the
      // page simply has to be loaded again.
      const orphaned = !(chrome.runtime && chrome.runtime.id);
      label.textContent = orphaned
        ? "Riplox updated — reload this page"
        : "Riplox is not reachable";
    }
    setTimeout(() => { label.textContent = original; busy = false; }, 2600);
  });

  /* ------------------------------------------------------- when to be here
   *
   * The button used to appear on every page it was injected into - a bank, an
   * inbox, a blank tab. It is only useful where there is something to
   * download, so it now waits until there is.
   *
   * The check is deliberately simple. A cleverer one that is wrong in ways
   * nobody can predict is worse than a plain one that is wrong predictably,
   * and guessing low costs very little: the toolbar icon works on every page,
   * and so does the right-click menu. A missing button costs a shortcut, not
   * a download.
   *
   * Two things it will not see, written down so nobody has to rediscover them:
   *   - video inside a cross-origin iframe. This runs in the top frame only,
   *     and injecting into every frame would put the button inside small
   *     embedded players, which is worse than not having it there.
   *   - video that does not exist until something is clicked. It appears the
   *     moment the element does - that is what the observer below is for.
   */

  const MIN_SIDE = 200;            // below this it is a thumbnail, not a video
  const MEDIA_EVENTS = ["loadedmetadata", "durationchange", "play", "emptied"];

  function worthIt(media) {
    // Something to play: a source of some kind, and either loaded metadata or
    // a real duration. An empty <video> placeholder has neither.
    if (!(media.currentSrc || media.src || media.querySelector("source"))) return false;
    if (!(media.readyState > 0 || (media.duration > 0 && isFinite(media.duration)))) return false;
    if (media.tagName === "AUDIO") return true;
    const box = media.getBoundingClientRect();
    const wide = Math.max(media.videoWidth || 0, box.width);
    const tall = Math.max(media.videoHeight || 0, box.height);
    return wide >= MIN_SIDE && tall >= MIN_SIDE;
  }

  function hasMedia() {
    for (const media of document.querySelectorAll("video, audio")) {
      if (worthIt(media)) return true;
    }
    return false;
  }

  let shown = false;
  let refused = false;             // this site is on the never list

  function show() {
    if (shown || refused) return;
    document.documentElement.appendChild(button);
    shown = true;
    restore();
  }

  function hide() {
    if (!shown) return;
    button.remove();
    shown = false;
  }

  /* A page can go from a video to no video without ever loading again - that
   * is an ordinary minute on any video site. Leaving the button behind would
   * be the same bug facing the other way, so this takes it away too. */
  function review() {
    if (refused || !hasMedia()) hide();
    else show();
  }

  let pending = 0;
  function reviewSoon() {
    // Already queued, so the rest of this burst is free. It used to clear and
    // remake the timer on every single mutation record - and on a video site
    // that is thousands of pairs a minute, spent to arrive at the same review
    // at the same moment. A measured study of 72 extensions found they cost
    // page-load energy even on pages they have nothing to do with; this was
    // this extension's share of that.
    if (pending) return;
    pending = setTimeout(() => { pending = 0; review(); }, 300);
  }

  const watcher = new MutationObserver(reviewSoon);
  watcher.observe(document.documentElement, { childList: true, subtree: true });

  /* Nothing left to watch for. On a site that has been dismissed the button can
   * never appear again, so keeping an observer on every DOM change is a cost
   * with no possible payoff. */
  function stopWatching() {
    watcher.disconnect();
    clearTimeout(pending);
    pending = 0;
    for (const name of MEDIA_EVENTS) {
      document.removeEventListener(name, reviewSoon, true);
    }
  }

  // Captured at the document, because none of these bubble.
  for (const name of MEDIA_EVENTS) {
    document.addEventListener(name, reviewSoon, true);
  }

  /* -------------------------------------------------- not on this site again
   *
   * The alternative to this is somebody turning the whole feature off because
   * of one site where it sits in the way. A cross costs one press, and it is
   * why the rest of the setting survives.
   */
  const dismiss = document.createElement("span");
  dismiss.textContent = "×";
  dismiss.title = "Do not show this on " + location.hostname;
  dismiss.setAttribute("role", "button");
  dismiss.setAttribute("aria-label", "Do not show this button on this site");
  Object.assign(dismiss.style, {
    marginLeft: "2px",
    color: "#64798F",
    fontWeight: "700",
    lineHeight: "1",
    cursor: "pointer",
  });
  dismiss.addEventListener("mouseenter", () => { dismiss.style.color = LOUD; });
  dismiss.addEventListener("mouseleave", () => { dismiss.style.color = "#64798F"; });
  // The thing it sits on is a drag handle. Without this, dismissing it would
  // start a drag instead.
  dismiss.addEventListener("pointerdown", (event) => event.stopPropagation());
  dismiss.addEventListener("click", (event) => {
    event.stopPropagation();
    event.preventDefault();
    refused = true;
    hide();
    stopWatching();
    try {
      chrome.storage.sync.get({ neverSites: [] }, (saved) => {
        const list = Array.isArray(saved.neverSites) ? saved.neverSites : [];
        if (!list.includes(location.hostname)) list.push(location.hostname);
        chrome.storage.sync.set({ neverSites: list.slice(-200) });
      });
    } catch (e) {
      // Storage refused. It is gone from this page, which is what was asked
      // for; it will just come back on the next one.
    }
  });
  button.appendChild(dismiss);

  try {
    chrome.storage.sync.get({ neverSites: [] }, (saved) => {
      const list = Array.isArray(saved.neverSites) ? saved.neverSites : [];
      refused = list.includes(location.hostname);
      if (refused) { stopWatching(); return; }
      review();
    });
  } catch (e) {
    review();
  }
})();
