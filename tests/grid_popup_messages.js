/*
 * Runs the SHIPPED popup.js - not a copy of it - against every state the
 * background worker can hand it, and reads what it says.
 *
 * popup.js reaches for the DOM and for chrome.* at load, so both are stubbed
 * just enough to let the file finish loading. Nothing about describe() or
 * olderThan() is reimplemented here; a grid that tests a copy proves the copy.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FILE = path.join(
  "C:", "Users", "FAIZAN COMPUTERS", "Desktop", "XniperBuilds", "RiploxDesktop",
  "browser-extension-store", "popup.js");

function fakeElement() {
  return {
    textContent: "", className: "", title: "", hidden: true, disabled: false,
    checked: false, classList: { add() {} }, addEventListener() {},
  };
}

const sandbox = {
  document: { getElementById: () => fakeElement() },
  chrome: {
    tabs: { query: async () => [{ url: "https://example.com/" }] },
    storage: {
      sync: { get: async (d) => ({ ...d }), set: async () => {} },
      session: { get: async (d) => ({ ...d }), remove: async () => {} },
    },
    runtime: { sendMessage: async () => null },
    permissions: { request: async () => false, remove: async () => {},
                   contains: async () => false },
  },
  setTimeout, console,
};
sandbox.window = { close() {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

const { describe, olderThan, usable } = sandbox;

let pass = 0, fail = 0;
const P1 = [];

function check(name, ok, detail) {
  if (ok) { pass += 1; } else { fail += 1; }
  console.log("  %s  %s%s", ok ? "PASS" : "FAIL", name, detail ? " | " + detail : "");
}

/* Wrong-vs-unhelpful is the whole point of this run: a message that sends
 * somebody to install software they already have is a defect, not a rough
 * edge. Each case declares the words that MUST NOT appear given its state. */
function truth(name, status, mustNot, mustHave) {
  const [text] = describe(status);
  const lied = mustNot.filter((w) => text.toLowerCase().includes(w.toLowerCase()));
  const missing = (mustHave || []).filter(
    (w) => !text.toLowerCase().includes(w.toLowerCase()));
  const ok = lied.length === 0 && missing.length === 0;
  if (!ok && lied.length) P1.push(name + " -> " + text);
  check(name, ok, ok ? text : "SAID: " + text
        + (lied.length ? "  [must not say: " + lied.join(", ") + "]" : "")
        + (missing.length ? "  [missing: " + missing.join(", ") + "]" : ""));
}

const MISSING = { ok: false, noHost: true, reason: "missing" };
const STALE = { ok: false, noHost: true, reason: "stale" };
const ok = (v, a, w, o) => ({ ok: true, version: v, active: a, waiting: w, oldest: o });

console.log("\n-- host absent -------------------------------------------------");
truth("says not installed", MISSING, ["update riplox", "working on"], ["not installed"]);

console.log("\n-- host present but refusing this extension ---------------------");
truth("does NOT say uninstalled", STALE, ["not installed"], ["1.5.0"]);

console.log("\n-- host answers, no version field (every Riplox before this one) -");
truth("no blank version printed", ok("", 0, 0, 0), ["riplox  is", "undefined"],
      ["older riplox", "1.5.0"]);

console.log("\n-- host answers, older version ---------------------------------");
truth("names the version it found", ok("1.4.1", 0, 0, 0), ["not installed"],
      ["1.4.1", "1.5.0"]);

console.log("\n-- host answers, current ---------------------------------------");
truth("idle says neither open nor closed", ok("1.5.0", 0, 0, 0),
      ["not installed", "or newer"], []);
truth("downloading says so", ok("1.5.0", 3, 0, 0), ["not installed", "or newer"],
      ["working on 3"]);
truth("just handed over", ok("1.5.0", 0, 1, 2), ["not installed", "riplox is not open"],
      ["just handed over"]);
truth("closed = links waiting", ok("1.5.0", 0, 2, 500), ["not installed"],
      ["not open", "2 links"]);

console.log("\n-- newer Riplox than this extension knows ------------------------");
truth("never nags on a newer app", ok("1.9.0", 1, 0, 0), ["or newer", "not installed"],
      ["working on 1"]);

console.log("\n-- the background itself did not answer --------------------------");
truth("null status", null, [], []);
truth("our worker silent != Riplox missing", { ok: false, noAnswer: true },
      ["not installed", "or newer"], ["could not check"]);

console.log("\n-- olderThan boundaries -----------------------------------------");
[["", true], ["1.4.9", true], ["1.5.0", false], ["1.5.1", false],
 ["1.10.0", false], ["2.0", false], ["junk", true], ["1.4", true]]
  .forEach(([v, want]) => check("olderThan(" + JSON.stringify(v) + ") = " + want,
                                olderThan(v, "1.5.0") === want,
                                String(olderThan(v, "1.5.0"))));
check("1.10.0 newer than 1.9.0", olderThan("1.10.0", "1.9.0") === false);

console.log("\n-- usable(url) boundaries --------------------------------------");
const long = "https://e.com/" + "a".repeat(2000);
[["https://a.com/", true], ["http://a.com/", true], ["chrome://extensions", false],
 ["", false], [null, false], ["ftp://a.com/", false],
 ["https://e.com/" + "a".repeat(1980), true], [long, false]]
  .forEach(([u, want]) => check("usable(" + String(u).slice(0, 28) + "…) = " + want,
                                usable(u) === want));

console.log("\n" + "=".repeat(66));
console.log("  %d passed, %d failed", pass, fail);
if (P1.length) {
  console.log("\n  P1 - MESSAGE IS WRONG, not merely unhelpful:");
  P1.forEach((p) => console.log("    " + p));
}
console.log("=".repeat(66));
process.exit(fail ? 1 : 0);
