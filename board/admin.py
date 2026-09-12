"""
The Board's admin door, from a terminal.

There is no admin page and there deliberately is not going to be one: a page
would need the key in a browser, and the key is the whole of the authority
here. This asks the Worker directly.

    python board/admin.py state
    python board/admin.py list
    python board/admin.py restore 12
    python board/admin.py delete 12
    python board/admin.py off | on
    python board/admin.py readonly | open
    python board/admin.py notice "Back in an hour"
    python board/admin.py notice --clear

The key is never typed at a prompt and never passed on the command line, where
it would sit in shell history. It is read, in this order, from:

    BOARD_KEY          the environment
    BOARD_KEY_FILE     a file named by the environment
    --key-file PATH    a file named here

A file may be the bare key on one line, or a line of the form BOARD_KEY=...
so that one file can hold both of the Worker's secrets.

Standard library only, so it runs anywhere the app already runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BOARD = os.environ.get("BOARD_URL", "https://board.xniperbuilds.com")


# --------------------------------------------------------------- the key

def read_key(key_file: str | None) -> str:
    direct = os.environ.get("BOARD_KEY", "").strip()
    if direct:
        return direct

    path = key_file or os.environ.get("BOARD_KEY_FILE", "")

    # Last resort, and the one that makes double-clicking this file work: a
    # file next to it holding the PATH to the key - a pointer, not a secret,
    # so it can say where this machine keeps things without this repo, which
    # is public, having to know.
    if not path:
        pointer = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".admin-key-path")
        try:
            path = open(pointer, encoding="utf-8").read().strip()
        except OSError:
            path = ""

    if not path:
        sys.exit("No key. Set BOARD_KEY or BOARD_KEY_FILE, pass --key-file, or put the\n"
                 "  path to the key file in board/.admin-key-path")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        sys.exit("Could not read the key file: %s" % exc)

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("BOARD_KEY="):
            return line.split("=", 1)[1].strip()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    sys.exit("That file has no key in it.")


# --------------------------------------------------------------- asking

def ask(path: str, body: dict, key: str = "") -> dict:
    """One call. The key travels in a header, never in the address."""
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "Riplox-admin"}
    if key:
        headers["X-Board-Key"] = key
    request = urllib.request.Request(BOARD + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, OSError):
            return {"ok": False, "error": "The board answered %s." % exc.code}
    except Exception as exc:                     # noqa: BLE001 - offline is normal
        return {"ok": False, "error": "Could not reach the board: %s" % exc}


def refused(answer: dict) -> bool:
    if answer.get("ok"):
        return False
    why = str(answer.get("error") or "something went wrong")
    if why == "No.":
        why = "No. - the key is wrong, or BOARD_KEY is not set on the Worker."
    print("  " + why)
    return True


def ago(ms: int) -> str:
    import time
    seconds = max(0, int(time.time() - ms / 1000))
    for size, name in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return "%d%s" % (seconds // size, name)
    return "just now"


# --------------------------------------------------------------- commands

def cmd_state(_a, _key):
    got = ask("/state", {})
    if refused(got):
        return 1
    print("  board:   %s" % ("ON" if got.get("on") else "OFF"))
    print("  posting: %s" % ("yes" if got.get("posting") else "no (read-only)"))
    print("  notice:  %s" % (got.get("notice") or "(none)"))
    print("  ping:    %ss" % got.get("ping"))
    return 0


def cmd_list(_a, key):
    got = ask("/admin/hidden", {}, key)
    if refused(got):
        return 1
    rows = got.get("rows") or []
    if not rows:
        print("  Nothing is hidden.")
        return 0
    print("  %-6s %-10s %-8s %-5s %s" % ("id", "platform", "age", "rep", "title / link"))
    for row in rows:
        print("  %-6s %-10s %-8s %-5s %s" % (
            row.get("id"), row.get("platform"), ago(row.get("at", 0)),
            row.get("reports"), (row.get("title") or row.get("url") or "")[:64]))
    print("\n  %d hidden. restore <id> puts one back, delete <id> removes it for good." % len(rows))
    return 0


def cmd_restore(a, key):
    if refused(ask("/admin/restore", {"id": a.id}, key)):
        return 1
    print("  %d is back on the board, and its reports are cleared - three new ones"
          "\n  would be needed to hide it again." % a.id)
    return 0


def cmd_delete(a, key):
    print("  Deleting %d cannot be undone. The board never deletes anything on its" % a.id)
    print("  own, so this is the only way a link leaves it.")
    if input("  Type the id again to confirm: ").strip() != str(a.id):
        print("  Left alone.")
        return 0
    if refused(ask("/admin/delete", {"id": a.id}, key)):
        return 1
    print("  Gone.")
    return 0


def flag(name, value, key, said):
    if refused(ask("/admin/flag", {"name": name, "value": value}, key)):
        return 1
    print("  " + said)
    return 0


def cmd_off(_a, key):
    return flag("off", "1", key, "The board is off. Nobody can read it or post to it.")


def cmd_on(_a, key):
    return flag("off", "", key, "The board is on again.")


def cmd_readonly(_a, key):
    return flag("readonly", "1", key,
                "Read-only. The board is still readable; nothing new can be posted.")


def cmd_open(_a, key):
    return flag("readonly", "", key, "The board is taking links again.")


def cmd_notice(a, key):
    if a.clear:
        return flag("notice", "", key, "The notice is gone.")
    if not a.text:
        return flag("notice", "", key, "Nothing to say, so the notice is gone.")
    text = " ".join(a.text).strip()
    if len(text) > 200:
        sys.exit("Keep it under 200 characters - it is one line above the paste box.")
    return flag("notice", text, key,
                "Set. The app reads this when somebody OPENS the Board, not while\n"
                "  they are already sitting on it - so anyone with the tab open sees\n"
                "  it the next time they come back to it:\n    " + text)


# --------------------------------------------------------------- watching

def cmd_waiting(_a, key):
    """
    For something that runs on a timer.

    Prints one line and nothing else, and says what it found in its exit code:
    0 there is nothing to do, 1 somebody should look, 2 the board could not be
    asked. A checker that cannot tell "nothing reported" from "could not ask"
    is a checker that goes quiet the day it matters.
    """
    rows = hidden_rows(key)
    if rows is None:
        print("unreachable")
        return 2
    print(len(rows))
    return 1 if rows else 0


# --------------------------------------------------------------- the menu

def hidden_rows(key):
    got = ask("/admin/hidden", {}, key)
    return None if not got.get("ok") else (got.get("rows") or [])


def board_state():
    got = ask("/state", {})
    return got if got.get("ok") else None


def draw(key):
    print("\n" + "=" * 62)
    print("  RIPLOX BOARD")
    print("=" * 62)

    state = board_state()
    if state is None:
        print("  The board could not be reached. It may be off, or this machine")
        print("  is offline. Nothing below will work until it answers.")
    else:
        print("  board:   %-8s posting: %s" % (
            "ON" if state.get("on") else "OFF",
            "yes" if state.get("posting") else "no (read-only)"))
        print("  notice:  %s" % (state.get("notice") or "(none)"))

    rows = hidden_rows(key)
    print("-" * 62)
    if rows is None:
        print("  Could not read the review list - is the key right?")
    elif not rows:
        print("  Nothing is waiting for you. Nobody has reported anything.")
    else:
        print("  %d LINK(S) REPORTED - they are hidden from everyone:\n" % len(rows))
        print("  %-6s %-10s %-7s %-4s %s" % ("id", "platform", "age", "rep", "title"))
        for row in rows:
            print("  %-6s %-10s %-7s %-4s %s" % (
                row.get("id"), row.get("platform"), ago(row.get("at", 0)),
                row.get("reports"), (row.get("title") or row.get("url") or "")[:40]))
    print("-" * 62)
    return rows


def pick(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def cmd_menu(_a, key):
    """Everything the admin door does, without a command to remember."""
    while True:
        rows = draw(key)
        print("  1  look again          4  write a notice")
        print("  2  put a link back     5  stop new posts / allow them again")
        print("  3  delete a link       6  turn the board off / on")
        print("  0  close")
        choice = pick("\n  > ")

        if choice in ("0", "q", ""):
            return 0
        if choice == "1":
            continue
        if choice in ("2", "3"):
            if not rows:
                print("\n  There is nothing hidden to act on.")
                pick("  Enter to go back ")
                continue
            which = pick("  Which id? ")
            if not which.isdigit():
                continue
            args = argparse.Namespace(id=int(which))
            (cmd_restore if choice == "2" else cmd_delete)(args, key)
            pick("\n  Enter to go back ")
        elif choice == "4":
            text = pick("  What should it say (empty to remove it)? ")
            cmd_notice(argparse.Namespace(text=[text] if text else [], clear=not text), key)
            pick("\n  Enter to go back ")
        elif choice == "5":
            state = board_state() or {}
            (cmd_open if not state.get("posting") else cmd_readonly)(None, key)
            pick("\n  Enter to go back ")
        elif choice == "6":
            state = board_state() or {}
            if state.get("on"):
                print("\n  Turning the board off means nobody can even read it.")
                if pick("  Type OFF to confirm: ") != "OFF":
                    continue
                cmd_off(None, key)
            else:
                cmd_on(None, key)
            pick("\n  Enter to go back ")


# --------------------------------------------------------------- cli

def main(argv=None):
    p = argparse.ArgumentParser(prog="board admin", description=__doc__.split("\n")[1])
    p.add_argument("--key-file", help="a file holding BOARD_KEY")
    sub = p.add_subparsers(dest="cmd")

    for name, fn, helptext in (
        ("state", cmd_state, "what the board says about itself (no key needed)"),
        ("list", cmd_list, "everything users have hidden, newest first"),
        ("off", cmd_off, "the kill switch - nobody can read or post"),
        ("on", cmd_on, "undo off"),
        ("readonly", cmd_readonly, "readable, but nothing new can be posted"),
        ("open", cmd_open, "undo readonly"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.set_defaults(func=fn)

    s = sub.add_parser("restore", help="put a hidden link back, and clear its reports")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("delete", help="remove a link for good - asks first")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("notice", help="one line shown above the paste box, for everyone")
    s.add_argument("text", nargs="*")
    s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_notice)

    s = sub.add_parser("menu", help="all of the above, without a command to remember")
    s.set_defaults(func=cmd_menu)

    s = sub.add_parser("waiting", help="exit 1 if something is hidden - for a scheduled check")
    s.set_defaults(func=cmd_waiting)

    # Double-clicked, or run with nothing after it: the menu is what anybody
    # wants. Printing help into a console that closes a tenth of a second
    # later is the same as printing nothing.
    a = p.parse_args(argv if argv is not None else (sys.argv[1:] or ["menu"]))
    if not getattr(a, "func", None):
        p.print_help()
        return 2

    if a.func not in (cmd_menu, cmd_waiting):
        print("  %s" % BOARD)
    key = "" if a.func is cmd_state else read_key(a.key_file)
    return a.func(a, key)


if __name__ == "__main__":
    # A window opened by double-clicking this file closes the instant the
    # script ends, which turns every message - including "no key" - into a
    # flash nobody can read. So when there were no arguments, wait.
    launched_by_hand = not sys.argv[1:]
    try:
        code = main()
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 1
        if stop.code and not isinstance(stop.code, int):
            print("\n  " + str(stop.code))
    if launched_by_hand:
        try:
            input("\n  Enter to close ")
        except (EOFError, KeyboardInterrupt):
            pass
    sys.exit(code)
