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
 */

(() => {
  if (window.__riploxButton) return;          // one per page, not per frame load
  window.__riploxButton = true;

  const ID = "riplox-send-button";
  if (document.getElementById(ID)) return;

  const button = document.createElement("button");
  button.id = ID;
  button.type = "button";
  button.textContent = "Send to Riplox";
  button.setAttribute("aria-label", "Send this page to Riplox");

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
    cursor: "pointer",
    boxShadow: "0 8px 24px -10px rgba(0,0,0,.7)",
    opacity: "0.55",
    transition: "opacity .15s ease",
  });

  button.addEventListener("mouseenter", () => { button.style.opacity = "1"; });
  button.addEventListener("mouseleave", () => { button.style.opacity = "0.55"; });

  let busy = false;
  button.addEventListener("click", async () => {
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
})();
