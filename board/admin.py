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
    if not path:
        sys.exit("No key. Set BOARD_KEY, or BOARD_KEY_FILE, or pass --key-file.")
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

    a = p.parse_args(argv)
    if not getattr(a, "func", None):
        p.print_help()
        return 2

    print("  %s" % BOARD)
    key = "" if a.func is cmd_state else read_key(a.key_file)
    return a.func(a, key)


if __name__ == "__main__":
    sys.exit(main())
