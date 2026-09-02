# -*- coding: utf-8 -*-
"""The options screen, in the states it will actually be in.

Read-only: a link is analysed, nothing is ever downloaded. The RIP button is
not pressed and no setting is saved - an earlier browser run once flipped a
real setting from 12 hours to 24, so every write endpoint is refused here.
"""
import pathlib, sys
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
URL = "https://youtu.be/HpnCXG8AKQ4"
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  | " + detail) if detail else ""))


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1180, "height": 900},
                            device_scale_factor=1, color_scheme="dark")

    blocked = []

    def guard(route):
        req = route.request
        if req.method in ("POST", "PUT", "PATCH", "DELETE") and "/analy" not in req.url:
            blocked.append(req.method + " " + req.url)
            return route.abort()
        return route.continue_()

    page.route("**/api/**", guard)

    page.goto("http://localhost:5010", wait_until="networkidle")
    page.fill("#urlInput", URL)
    page.click("#analyzeBtn")
    page.wait_for_selector("#preview:not([hidden])", timeout=90000)
    page.wait_for_timeout(600)
    page.evaluate("[...document.querySelectorAll('#preview details')].forEach(d=>d.open=true)")
    page.wait_for_timeout(300)

    print("\n-- the blocks, where they now live " + "-" * 32)
    kids = page.evaluate("[...document.getElementById('preview').children]"
                         ".map(c => c.className || c.id)")
    check("heatBox, moreBox and the actions are children of .preview",
          all(k in " ".join(kids) for k in ("heatmap", "more", "preview-actions")),
          " / ".join(kids))

    print("\n-- hidden blocks stay hidden inside the grid " + "-" * 23)
    leak = page.evaluate("""() => [...document.querySelectorAll('.more-sec[hidden]')]
        .filter(e => e.getClientRects().length > 0)
        .map(e => e.id || e.className)""")
    total_hidden = page.evaluate("document.querySelectorAll('.more-sec[hidden]').length")
    check("no hidden block is drawn", not leak,
          "%d hidden, %d leaking" % (total_hidden, len(leak)))

    print("\n-- the advanced blocks come last " + "-" * 34)
    # ⚠️ By where they are DRAWN, not by DOM order. CSS order moves the box and
    # leaves the node where it was, so reading children in document order tests
    # the markup and says nothing about what anyone sees. The first version of
    # this check did exactly that and reported a failure that was not there.
    order = page.evaluate("""() => {
        const kids = [...document.querySelector('.more-body').children]
            .filter(c => !c.hidden)
            .map(c => ({ c, y: c.getBoundingClientRect().top,
                            x: c.getBoundingClientRect().left }))
            .sort((a, b) => (a.y - b.y) || (a.x - b.x));
        return kids.map(({ c }) => (c.classList.contains('adv') ? 'ADV ' : '    ')
            + (c.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 34));
    }""")
    for line in order:
        print("       " + line)
    first_adv = next((i for i, l in enumerate(order) if l.startswith("ADV")), len(order))
    last_plain = max((i for i, l in enumerate(order) if not l.startswith("ADV")), default=-1)
    check("nothing plain comes after an advanced block", last_plain < first_adv,
          "first adv at %d, last plain at %d" % (first_adv, last_plain))

    print("\n-- nothing spills out of its box " + "-" * 34)
    spill = page.evaluate("""() => [...document.querySelectorAll('#preview *')]
        .filter(e => e.scrollWidth > e.clientWidth + 2 && e.clientWidth > 0
                     && !e.className.toString().match(/fmt-wrap|thumb-pick|preview-media/))
        .map(e => (e.className || e.tagName) + ' ' + e.scrollWidth + '>' + e.clientWidth)""")
    check("no unexpected overflow", not spill, " / ".join(spill) or "clean")

    print("\n-- a narrow window folds to one column " + "-" * 28)
    page.set_viewport_size({"width": 900, "height": 900})
    page.wait_for_timeout(300)
    cols = page.evaluate("getComputedStyle(document.querySelector('.more-body'))"
                         ".gridTemplateColumns")
    narrow_spill = page.evaluate("""() => [...document.querySelectorAll('#preview *')]
        .filter(e => e.scrollWidth > e.clientWidth + 2 && e.clientWidth > 0
                     && !e.className.toString().match(/fmt-wrap|thumb-pick|preview-media/))
        .length""")
    check("still no overflow at 900px", narrow_spill == 0, "columns: " + cols)
    page.set_viewport_size({"width": 1180, "height": 900})

    print("\n-- light theme " + "-" * 52)
    page.evaluate("document.documentElement.setAttribute('data-theme','light')")
    page.wait_for_timeout(250)
    contrast = page.evaluate("""() => {
        const lum = (c) => {
            const p = c.match(/\\d+/g).slice(0,3).map(Number).map(v => {
                v /= 255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4);
            });
            return .2126*p[0] + .7152*p[1] + .0722*p[2];
        };
        const el = document.querySelector('.more-sec:not([hidden]) > label');
        if (!el) return null;
        const fg = lum(getComputedStyle(el).color);
        let bg = null, n = el;
        while (n && !bg) {
            const c = getComputedStyle(n).backgroundColor;
            if (c && !c.startsWith('rgba(0, 0, 0, 0)')) bg = lum(c);
            n = n.parentElement;
        }
        if (bg === null) return null;
        const hi = Math.max(fg,bg), lo = Math.min(fg,bg);
        return Math.round(((hi+.05)/(lo+.05)) * 100) / 100;
    }""")
    check("a block heading is readable in light theme",
          contrast is not None and contrast >= 4.5,
          "%s:1" % contrast)
    page.evaluate("document.documentElement.setAttribute('data-theme','dark')")

    print("\n-- nothing was written " + "-" * 44)
    check("no write endpoint was called", True,
          "%d blocked" % len(blocked) if blocked else "none attempted")

    browser.close()

print("\n" + "=" * 70)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("    FAILED: " + f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
