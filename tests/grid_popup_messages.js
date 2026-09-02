/*
 * Runs the SHIPPED popup.js - not a copy of it - against every state the
 * background worker can hand it, and reads what it says.
 *
 * popup.js reaches for the DOM and for chrome.* at load, so both are stubbed
 * just enough to let the file finish loading. Nothing about describe() or
 * olderThan() is reimplemented here; a grid that tests a copy proves the copy.
 *
 * The middle section drives showState() rather than describe(). That
 * distinction is not academic: an earlier version of this file only called
 * describe(), and two real defects walked straight through it - a getEl that
 * was used and never declared, so showState() threw before it could set the
 * link's words, and a CSS rule that outranked the hidden attribute so the link
 * showed in every state including the healthy ones. Both lived in the lines
 * describe() never touches. A grid that stops at the pure function proves the
 * pure function.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FILE = path.join(__dirname, "..", "browser-extension-store", "popup.js");

// One element per id, so what the code sets can be read back afterwards.
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id, textContent: "", className: "", title: "", hidden: true,
      disabled: false, checked: false,
      classList: { add() {} }, addEventListener() {},
    });
  }
  return elements.get(id);
}

let reply = null;                       // what the worker answers this round

const sandbox = {
  document: { getElementById: element },
  chrome: {
    tabs: { query: async () => [{ url: "https://example.com/" }] },
    storage: {
      sync: { get: async (d) => ({ ...d }), set: async () => {} },
      session: { get: async (d) => ({ ...d }), remove: async () => {} },
    },
    runtime: { sendMessage: async () => reply },
    permissions: { request: async () => false, remove: async () => {},
                   contains: async () => false },
  },
  setTimeout, console, URL,
};
sandbox.window = { close() {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

const { describe, olderThan, usable, showState } = sandbox;

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
  const gone = (mustHave || []).filter(
    (w) => !text.toLowerCase().includes(w.toLowerCase()));
  const ok = lied.length === 0 && gone.length === 0;
  if (!ok && lied.length) P1.push(name + " -> " + text);
  check(name, ok, ok ? text : "SAID: " + text
        + (lied.length ? "  [must not say: " + lied.join(", ") + "]" : "")
        + (gone.length ? "  [missing: " + gone.join(", ") + "]" : ""));
}

const MISSING = { ok: false, noHost: true, reason: "missing" };
const STALE = { ok: false, noHost: true, reason: "stale" };
const NO_ANSWER = { ok: false, noAnswer: true };
const ok = (v, a, w, o) => ({ ok: true, version: v, active: a, waiting: w, oldest: o });

console.log("\n-- what it says ------------------------------------------------");
truth("absent: says not installed", MISSING, ["update riplox", "working on"],
      ["not installed"]);
truth("refused: not uninstalled, and not called old", STALE,
      ["not installed", "older", "1.5.0"], ["does not know about this extension"]);
truth("no version field: no blank, and not called old", ok("", 0, 0, 0),
      ["riplox  is", "undefined", "older", "1.5.0"],
      ["does not know about this extension"]);
truth("old version: names what it found", ok("1.4.1", 0, 0, 0), ["not installed"],
      ["1.4.1", "1.5.0"]);
truth("current + idle: claims neither", ok("1.5.0", 0, 0, 0),
      ["not installed", "or newer"], []);
truth("current + busy", ok("1.5.0", 3, 0, 0), ["not installed", "or newer"],
      ["working on 3"]);
truth("just handed over", ok("1.5.0", 0, 1, 2),
      ["not installed", "riplox is not open"], ["just handed over"]);
truth("closed: links waiting", ok("1.5.0", 0, 2, 500), ["not installed"],
      ["not open", "2 links"]);
truth("newer app: never nags", ok("1.9.0", 1, 0, 0), ["or newer", "not installed"],
      ["working on 1"]);
truth("null status", null, [], []);
truth("our worker silent != Riplox missing", NO_ANSWER,
      ["not installed", "or newer"], ["could not check"]);

/* ---------------------------------------------------------------- the link
 *
 * Driven through showState, because the words and the hiding both live there.
 * Offering a download to somebody whose Riplox is running is the same class of
 * mistake as telling them it is not installed. */
async function link(name, status, visible, words) {
  reply = status;
  try {
    await showState();
  } catch (e) {
    /* A throw here used to end the whole run silently, and the mutation pass
     * read that silence as "nothing failed" - which is how an undeclared getEl
     * escaped a grid written to catch exactly that. A check that cannot finish
     * has failed; it has not passed. */
    check(name, false, "THREW: " + e.message);
    return;
  }
  const el = element("get");
  const shown = el.hidden === !visible;
  const said = !visible || el.textContent.toLowerCase().includes(words);
  check(name, shown && said,
        "hidden=" + el.hidden + " text=" + JSON.stringify(el.textContent));
}

(async () => {
  console.log("\n-- the way-out link --------------------------------------------");
  await link("absent -> Get", MISSING, true, "get riplox");
  await link("refused -> Update", STALE, true, "update riplox");
  await link("no version -> Update", ok("", 0, 0, 0), true, "update riplox");
  await link("old version -> Update", ok("1.4.1", 0, 0, 0), true, "update riplox");
  await link("current -> nothing offered", ok("1.5.0", 2, 0, 0), false, "");
  await link("worker silent -> nothing offered", NO_ANSWER, false, "");

  console.log("\n-- olderThan boundaries ----------------------------------------");
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
    .forEach(([u, want]) => check("usable(" + String(u).slice(0, 28) + "...) = " + want,
                                  usable(u) === want));

  console.log("\n" + "=".repeat(66));
  console.log("  " + pass + " passed, " + fail + " failed");
  if (P1.length) {
    console.log("\n  P1 - MESSAGE IS WRONG, not merely unhelpful:");
    P1.forEach((p) => console.log("    " + p));
  }
  console.log("=".repeat(66));
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  // Nothing below this point ran, so nothing below it is proven.
  console.log("  FAIL  the run itself threw | " + e.message);
  console.log("=".repeat(66));
  process.exit(1);
});
