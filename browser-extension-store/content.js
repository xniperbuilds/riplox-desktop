/*
 * The in-page button.
 *
 * Injected only after someone turns it on and the browser grants access to
 * pages - it is not part of what anyone installs. That order matters: this
 * extension can see nothing unless asked, and a button on every page is
 * exactly the kind of thing that quietly ends up reading every page.
 *
 * It reads the address of the page it is on and hands that to the background
 * worker, same as the toolbar button. It never touches a video, a player, or
 * any element of the page.
 *
 * WHAT THIS BUILD DELIBERATELY DOES NOT DO, and why it matters here:
 *
 * The bundled build decides whether to appear by looking for a <video> or
 * <audio> element big enough to be worth downloading. That is a useful trick
 * and it is the wrong one for a store listing: an extension that inspects a
 * page, identifies media on it, and offers to fetch it is describing itself as
 * something the Chrome Web Store does not allow.
 *
 * So this one does not look at the page at all. It is a shortcut to the same
 * thing the toolbar button does, sitting where the mouse already is - shown
 * wherever access was granted, and dismissed per site by the person who does
 * not want it there. No querySelector, no MutationObserver, no media events.
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

  const QUIET = "#9FB3C8";          // ~8.7:1 on the body. Quiet, not faint.
  const LOUD = "#E8F6F8";           // ~16.9:1

  const button = document.createElement("button");
  button.id = ID;
  button.type = "button";

  /* The mark.
   *
   * Riplox's own arrow, drawn rather than typed. A text arrow is whatever glyph
   * the page's font happens to have and reads as punctuation; this is the shape
   * the app and the installer use, so what lands on the page is recognisably
   * this product rather than the grey pill every extension puts there.
   *
   * Built with createElementNS and attributes, never innerHTML: sites that
   * enforce Trusted Types refuse innerHTML outright, and the button would
   * simply never appear on them.
   */
  const NS = "http://www.w3.org/2000/svg";

  function stroke(d) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#05202B");
    path.setAttribute("stroke-width", "1.9");
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
    // as one object is why the silhouette is memorable.
    borderRadius: "8px 8px 8px 3px",
    background: "linear-gradient(160deg, #5BE1F5 0%, #22B8CF 100%)",
    boxShadow: "inset 0 1px 0 rgba(255,255,255,.45)",
  });
  button.appendChild(mark);

  /* The words live in their own element rather than on the button: the click
   * handler rewrites them to say what happened, and doing that with
   * button.textContent would delete the mark and the dismiss cross with it. */
  const label = document.createElement("span");
  label.textContent = "Send to Riplox";
  button.appendChild(label);

  button.setAttribute("aria-label", "Send this page to Riplox. Drag to move it.");
  button.title = "Send this page to Riplox — drag to move";

  /* All of it inline: a page's own stylesheet must not be able to make this
   * look like part of the site, and this must not leak style into the page.
   *
   * Why it stays readable on a white article and on a black video frame alike:
   * it is fully opaque, and the separation comes from two rings - a white
   * hairline immediately outside the dark body, and a dark halo outside that.
   * Against black the white ring cuts it out; against white the dark halo does.
   * Neither ring has to know what is behind it, which is the point, because
   * this lands on pages nobody has seen. A half-transparent button has no
   * colour of its own and there is no opacity value that is right everywhere.
   *
   * The one square corner is deliberate: an evenly rounded pill is the shape
   * that disappears into whatever site it is sitting on.
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
    font: '500 13px/1 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
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
      // Two very different things arrive here, and calling both "not
      // reachable" sends somebody to check an app that is running perfectly.
      // When the extension is reloaded or updated, every content script already
      // on a page is orphaned - its chrome.runtime is dead and its id is gone.
      const orphaned = !(chrome.runtime && chrome.runtime.id);
      label.textContent = orphaned ? "Riplox updated — reload this page"
                                   : "Riplox is not reachable";
    }
    setTimeout(() => { label.textContent = original; busy = false; }, 2600);
  });

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
    button.remove();
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

  /* ------------------------------------------------------------ appearing
   *
   * Straight away, unless this site has been dismissed. Nothing is inspected
   * and nothing is waited for - the page is not this extension's business.
   */
  try {
    chrome.storage.sync.get({ neverSites: [] }, (saved) => {
      const list = Array.isArray(saved.neverSites) ? saved.neverSites : [];
      if (list.includes(location.hostname)) return;
      document.documentElement.appendChild(button);
      restore();
    });
  } catch (e) {
    document.documentElement.appendChild(button);
    restore();
  }
})();
