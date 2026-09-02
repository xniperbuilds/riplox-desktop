# -*- coding: utf-8 -*-
"""The hidden attribute must actually hide, in a real engine.

The JS grid reads element.hidden, which is a property. Whether the pixel is
drawn is decided by CSS, and a class rule outranks the user-agent rule behind
the hidden attribute - which is exactly how the "Get Riplox" link came to show
on top of a perfectly healthy Riplox. That defect is invisible to the grid by
construction, so it is measured here instead: a real browser, the real
stylesheet, and getComputedStyle.
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

POPUP = (pathlib.Path(__file__).resolve().parent.parent
         / "browser-extension-store" / "popup.html")

# popup.js runs on load and needs chrome.* to exist; none of it matters here.
SHIM = """
window.chrome = {
  tabs: { query: async () => [{ url: "https://example.com/" }] },
  storage: {
    sync: { get: async (d) => ({ ...d }), set: async () => {} },
    session: { get: async (d) => ({ ...d }), remove: async () => {} },
  },
  runtime: { sendMessage: async () => ({ ok: true, version: "1.5.0",
                                         active: 1, waiting: 0, oldest: 0 }) },
  permissions: { contains: async () => false, request: async () => false,
                 remove: async () => {} },
};
"""

CASES = [
    ("get", "the way-out link"),
    ("back", "the put-it-back button"),
]

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + detail) if detail else ""))


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(color_scheme="dark")
    page.add_init_script(SHIM)
    page.goto(POPUP.as_uri(), wait_until="networkidle")
    page.wait_for_timeout(300)

    print("\n-- hidden actually hides " + "-" * 40)
    for el_id, label in CASES:
        display = page.evaluate(
            """(id) => {
                 const el = document.getElementById(id);
                 el.hidden = true;
                 return getComputedStyle(el).display;
               }""", el_id)
        check("%s: hidden -> display:none" % label, display == "none", display)

    print("\n-- and showing actually shows " + "-" * 35)
    for el_id, label in CASES:
        display = page.evaluate(
            """(id) => {
                 const el = document.getElementById(id);
                 el.hidden = false;
                 return getComputedStyle(el).display;
               }""", el_id)
        check("%s: shown -> display:block" % label, display == "block", display)

    print("\n-- nothing overflows its box " + "-" * 36)
    overflow = page.evaluate(
        """() => {
             const body = document.body;
             return [body.scrollWidth, body.clientWidth];
           }""")
    check("popup does not scroll sideways", overflow[0] <= overflow[1] + 1,
          "scrollWidth=%s clientWidth=%s" % tuple(overflow))

    browser.close()

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 68)
sys.exit(1 if FAIL else 0)
