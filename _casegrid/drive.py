"""
Run every case in the grid, each in its own process and its own sandbox, and
write what happened back into the ledger.

A case is only marked PROVEN here if the runner actually ran and its output
was read - the status comes from the exit and the text, never from this file's
opinion of the code.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRID = HERE / "grid-1.6.0.json"
RUNNER = HERE / "runner.py"
PY = sys.executable

grid = json.loads(GRID.read_text(encoding="utf-8"))
only = sys.argv[1:] if len(sys.argv) > 1 else None

done = {"PROVEN": 0, "FAILED": 0, "UNVERIFIED": 0, "BLOCKED": 0}

for case in grid["cases"]:
    if case.get("kind") != "grid":
        continue                          # repros and sequences run separately
    if only and str(case["id"]) not in only:
        continue

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(case, fh)
        payload = fh.name

    try:
        out = subprocess.run([PY, str(RUNNER), payload], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=180)
        text = (out.stdout or "") + (out.stderr or "")
    except subprocess.TimeoutExpired:
        text = "TIMED OUT after 180s"
        out = None
    finally:
        Path(payload).unlink(missing_ok=True)

    # ⚠ The verdict comes from the text, not the exit code: plenty of tools
    # exit 0 while printing failures, and PowerShell reports failure for a
    # program that merely wrote to stderr.
    first = text.strip().splitlines()[0] if text.strip() else ""
    if first.startswith("RESULT ok"):
        case["status"] = "PROVEN"
    elif first.startswith("RESULT fail"):
        case["status"] = "FAILED"
    elif "TIMED OUT" in text:
        case["status"] = "BLOCKED"
    else:
        case["status"] = "UNVERIFIED"

    keep = [l for l in text.splitlines() if "BAD" in l or l.startswith("  ")]
    case["evidence"] = "\n".join(keep[:14]) or text[:600]
    done[case["status"]] = done.get(case["status"], 0) + 1

    mark = {"PROVEN": ".", "FAILED": "F", "BLOCKED": "B"}.get(case["status"], "?")
    print(mark, end="", flush=True)
    if case["status"] != "PROVEN":
        print(f"\ncase {case['id']} {case['status']}: {case['values']}")
        for line in [l for l in text.splitlines() if "BAD" in l][:6]:
            print("   " + line.strip())

GRID.write_text(json.dumps(grid, indent=2), encoding="utf-8")
print("\n" + json.dumps(done))
