"""The three defaults a real machine was found in the wrong state on.

Reported as the https error coming back again and again, then reproduced on a
second laptop. What was wrong there was not code: the engine was months old,
pieces per file was 4, and the YouTube helper was off. Turning all three round
made the downloads run.

So they are defaults with a test on them. A default that quietly goes back to
what it was is the same fault again, and it would not show up in any other
check here - everything else passes perfectly well with a stale engine.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))
import engine                                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


D = engine.DEFAULT_SETTINGS

print("-- what a fresh install starts with " + "-" * 34)
check("the YouTube helper is on", D.get("potoken") is True, D.get("potoken"))
check("pieces per file is 16", D.get("fragments") == 16, D.get("fragments"))
check("the engine keeps itself current", D.get("engine_auto") is True,
      D.get("engine_auto"))

# ⚠️ save_settings drops any key it does not know, so a setting that is not in
# DEFAULT_SETTINGS can never persist - it would read as "on" for one run and
# then be gone. That is why this is checked rather than assumed.
print("\n-- and they survive being saved " + "-" * 38)
for key in ("potoken", "fragments", "engine_auto"):
    check("%s is a key the app knows" % key, key in D)

print("\n-- the engine is not swapped under a running download " + "-" * 16)
sys.path.insert(0, str(SRC))
import app                                                 # noqa: E402

real = app.manager.snapshot
try:
    app.manager.snapshot = lambda: [{"status": "downloading"}]
    check("busy while something downloads", app._engine_busy() is True)
    app.manager.snapshot = lambda: [{"status": "done"}, {"status": "error"}]
    check("free when nothing is", app._engine_busy() is False)
    app.manager.snapshot = lambda: [{"status": "paused"}]
    # Paused counts as active in the manager's own list, and a paused job
    # resumes into the engine that is on disk at that moment.
    check("a paused job still counts as busy", app._engine_busy() is True)

    def boom():
        raise RuntimeError("no")
    app.manager.snapshot = boom
    check("unsure means leave it alone", app._engine_busy() is True)
finally:
    app.manager.snapshot = real

print("\n-- and it is fetched, not just reported " + "-" * 30)
# ⚠️ The whole point of the change, and the part a live run cannot show: on
# this machine the engine is already the newest one published, so nothing was
# ever going to be downloaded. check_engine_update and update_engine are stood
# in for, so what is being tested is the wiring - does a "newer" verdict lead
# to a fetch - and not GitHub's release schedule. Nothing is downloaded here.
real_check, real_update = engine.check_engine_update, engine.update_engine
real_snapshot, real_load = app.manager.snapshot, engine.load_settings
fetched = []

try:
    engine.update_engine = lambda *a, **k: fetched.append(True) or {"ok": True}
    engine.check_engine_update = lambda force=False: {"ok": True, "newer": True,
                                                      "latest": "2099.01.01"}
    app.manager.snapshot = lambda: []

    with app.app.test_request_context("/api/check-engine", json={}):
        del fetched[:]
        engine.load_settings = lambda: {"engine_auto": True}
        app.api_check_engine()
        # The thread is spawned; give it a moment to run.
        import time
        for _ in range(40):
            if fetched:
                break
            time.sleep(0.05)
        check("a newer engine is fetched, not just announced", bool(fetched))

    with app.app.test_request_context("/api/check-engine", json={}):
        del fetched[:]
        engine.load_settings = lambda: {"engine_auto": False}
        app.api_check_engine()
        time.sleep(0.4)
        check("and not when the setting is off", not fetched)

    with app.app.test_request_context("/api/check-engine", json={}):
        del fetched[:]
        engine.load_settings = lambda: {"engine_auto": True}
        app.manager.snapshot = lambda: [{"status": "downloading"}]
        app.api_check_engine()
        time.sleep(0.4)
        check("and never under a running download", not fetched)
finally:
    engine.check_engine_update, engine.update_engine = real_check, real_update
    app.manager.snapshot, engine.load_settings = real_snapshot, real_load

print("\n-- the new-version notice is in every room " + "-" * 27)
html = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
bar = html.find('id="updateBar"')
main = html.find("<main>")
first_view = html.find('<section class="view')
check("the bar exists", bar > 0)
check("it sits inside main", main > 0 and bar > main, "main at %d, bar at %d" % (main, bar))
# ⚠️ The point of the move. It used to live in the Queue screen - the one room
# somebody who is not downloading anything never opens - so the only people
# told about a new Riplox were the ones already busy.
check("and before the first view, so no room hides it",
      0 < bar < first_view, "bar at %d, first view at %d" % (bar, first_view))
inside_a_view = re.search(r'<section class="view[^>]*>(?:(?!</section>).)*?id="updateBar"',
                          html, re.S)
check("it is not inside any view", inside_a_view is None)
check("it can be put off, not killed", 'id="updateLater"' in html)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
