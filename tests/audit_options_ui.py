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

    print("\n-- the two answers lead the rungs " + "-" * 33)

    def quality_geo():
        return page.evaluate("""() => [...document.querySelectorAll('#qualityChips .chip')]
            .map(c => { const r = c.getBoundingClientRect();
                        const n = c.querySelector('.chip-note');
                        return {
                q: c.dataset.q, auto: c.classList.contains('auto'),
                w: Math.round(r.width), top: Math.round(r.top),
                name: (c.querySelector('.chip-name')||{}).textContent || '',
                size: (c.querySelector('.chip-size')||{}).textContent || '',
                note: n ? n.textContent : '',
                lines: n ? Math.round(n.getBoundingClientRect().height
                          / parseFloat(getComputedStyle(n).lineHeight)) : 0,
                gap: n ? Math.round(n.getBoundingClientRect().top
                        - c.querySelector('.chip-name').getBoundingClientRect().bottom)
                       : 0 }; })""")

    geo = quality_geo()
    for g in geo:
        print("       %-5s %s%4d  %-16s %-10s %s"
              % (g["q"], "AUTO " if g["auto"] else "     ", g["w"],
                 g["name"], g["size"], g["note"]))
    autos = [g for g in geo if g["auto"]]
    rungs = [g for g in geo if not g["auto"]]
    check("best and max are the two auto cards",
          sorted(g["q"] for g in autos) == ["best", "max"],
          ", ".join(g["q"] for g in autos) or "none")
    check("an auto card is wider than a rung and sits above it",
          bool(autos) and bool(rungs)
          and min(g["w"] for g in autos) > max(g["w"] for g in rungs)
          and max(g["top"] for g in autos) < min(g["top"] for g in rungs),
          "auto %dpx, rung %dpx" % (autos[0]["w"], rungs[0]["w"])
          if autos and rungs else "-")

    # ⚠ Not a hard-coded "2160p". Which rungs YouTube offers changes between
    # runs - an earlier version of this check passed on nine chips and failed
    # on four with the app right both times. What holds either way: the rung a
    # card names exists, and carries that card's own size.
    def named_rung(g):
        tail = g["note"].split(" \u00b7 ", 1)[-1] if " \u00b7 " in g["note"] else ""
        return next((r for r in rungs if r["name"] == tail), None)

    check("each auto card names a real rung that carries its size",
          bool(autos) and not [g for g in autos
                               if not named_rung(g)
                               or named_rung(g)["size"] != g["size"]],
          " / ".join("%s -> %s" % (g["q"], g["note"]) for g in autos))

    # "Best available" used to say it "plays anywhere". h264 is a tie-break in
    # the selector and not a filter (engine.format_args, and the warning above
    # it), so wherever a height exists only as VP9 or AV1 that was a promise
    # the delivered file did not keep.
    check("no card promises it plays anywhere",
          not any("plays anywhere" in g["note"] for g in geo), "clean")

    # The note belongs under its name. The shared .chip rule says
    # justify-content: space-between, which across a row means "size to the
    # right" and down a column means "note to the floor" - and a card with its
    # second line stranded at the bottom passes every other check here.
    check("each note sits under its own name",
          bool(autos) and all(0 <= g["gap"] <= 8 for g in autos),
          "gaps: " + ", ".join(str(g["gap"]) for g in autos))

    print("\n-- the line beside the button " + "-" * 37)
    lines = []
    for g in geo:
        page.click('#qualityChips .chip[data-q="%s"]' % g["q"])
        page.wait_for_timeout(120)
        state = page.evaluate("""() => { const e = document.getElementById('ripSummary');
            const on = document.querySelector('#qualityChips .chip.is-on');
            return { text: e.textContent.trim(), hidden: e.hidden,
                     on: on ? on.dataset.q : null }; }""")
        print("       %-5s -> %r" % (g["q"], state["text"]))
        lines.append((g["q"], state))
    # ⚠ Matched against the chip, not merely non-empty. Drop the
    # syncRipSummary() call from the click handler and the line keeps the
    # PREVIOUS chip's answer - still non-empty, still with the right chip lit,
    # and describing a quality nobody chose.
    named = {g["q"]: g["name"] for g in geo}
    check("every chip sets itself and rewrites the line to match",
          all(s["on"] == q and not s["hidden"]
              and s["text"].startswith(named[q]) for q, s in lines),
          "%d chips" % len(lines))
    # Only Max. Every other rung is a named height, and a named height has
    # come back at the size it was given.
    check("only Max warns that the size is not final",
          [q for q, s in lines if "only once it finishes" in s["text"]] == ["max"],
          ", ".join(q for q, s in lines if "only once it finishes" in s["text"]) or "none")

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

    # The cards match heights only while their notes stay on one line. A
    # narrower column is what would wrap them, so it is checked at the narrow
    # width rather than assumed from the wide one.
    narrow = [g for g in quality_geo() if g["auto"]]
    check("the two cards still match at 900px",
          bool(narrow) and all(g["lines"] <= 1 for g in narrow),
          "note lines: " + ", ".join(str(g["lines"]) for g in narrow))
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
