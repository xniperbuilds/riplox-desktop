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
  button.textContent = "Send to Riplox";
  button.setAttribute("aria-label", "Send this page to Riplox. Drag to move it.");
  button.title = "Send this page to Riplox — drag to move";

  // All of it inline: a page's own stylesheet must not be able to make this
  // look like part of the site, and this must not leak style into the page.
  Object.assign(button.style, {
    position: "fixed",
    right: "18px",
    bottom: "18px",
    zIndex: "2147483647",
    padding: "9px 15px",
    border: "1px solid rgba(255,255,255,.18)",
    borderRadius: "9px",
    background: "#0f1720",
    color: "#dff7f5",
    font: "600 13px/1.2 system-ui, sans-serif",
    cursor: "grab",
    boxShadow: "0 8px 24px -10px rgba(0,0,0,.7)",
    opacity: "0.55",
    transition: "opacity .15s ease",
    touchAction: "none",
    userSelect: "none",
  });

  button.addEventListener("mouseenter", () => { button.style.opacity = "1"; });
  button.addEventListener("mouseleave", () => {
    if (!dragging) button.style.opacity = "0.55";
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
    button.style.opacity = "1";
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
    button.style.transition = "opacity .15s ease";
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
    const original = button.textContent;
    button.textContent = "Sending…";
    try {
      const answer = await chrome.runtime.sendMessage({
        kind: "send", url: location.href,
      });
      // Says what actually happened, and never claims a download: handing the
      // address over is all this can know about.
      button.textContent = !answer ? "Riplox did not answer"
        : answer.ok ? (answer.via === "host" ? "Sent" : "Allow it in the new tab")
        : (answer.error || "Could not send");
    } catch (e) {
      button.textContent = "Riplox is not reachable";
    }
    setTimeout(() => { button.textContent = original; busy = false; }, 2600);
  });

  document.documentElement.appendChild(button);
  restore();
})();
