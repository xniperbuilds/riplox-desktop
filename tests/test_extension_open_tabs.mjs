/*
 * Turning the in-page button on must reach the page you are looking at.
 *
 * The bug this guards, reported from a real YouTube tab: registerContentScripts
 * only injects into pages loaded AFTER it runs. Somebody who ticks "Show a
 * button on every page" is staring at a page right now, and nothing appearing
 * on it is indistinguishable from the feature being broken. It stayed broken
 * until the tab happened to be reloaded.
 *
 * Runs the REAL background.js in a VM. A test carrying its own copy of the
 * logic would prove only that the copy agrees with itself.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "browser-extension", "background.js"), "utf-8");

let passed = 0;
const failures = [];
function check(name, ok, detail = "") {
  if (ok) { passed++; console.log("  PASS  " + name); }
  else { failures.push(name); console.log("  FAIL  " + name + (detail ? " | " + detail : "")); }
}

const noop = () => {};
const listener = { addListener: noop, removeListener: noop };

/* A fresh extension world for each case, so one test cannot colour the next. */
function world({ inPageButton = true, granted = true, tabs = [],
                 queryThrows = false, scriptThrows = false } = {}) {
  const seen = { registered: [], unregistered: [], injected: [], queried: null };

  const context = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    URL, URLSearchParams, JSON, Math, Date, Promise,
    chrome: {
      runtime: { onInstalled: listener, onStartup: listener, onMessage: listener,
                 sendNativeMessage: async () => ({}), lastError: null, id: "test" },
      contextMenus: { create: noop, removeAll: (cb) => cb && cb(), onClicked: listener },
      storage: {
        sync: { get: async () => ({ inPageButton, sites: [] }), set: async () => {} },
        onChanged: listener,
      },
      scripting: {
        registerContentScripts: async (list) => { seen.registered.push(list); },
        unregisterContentScripts: async (what) => { seen.unregistered.push(what); },
        getRegisteredContentScripts: async () => [],
        executeScript: async (opts) => {
          if (scriptThrows) throw new Error("cannot script this page");
          seen.injected.push(opts.target.tabId);
        },
      },
      permissions: { contains: async () => granted, onRemoved: listener, onAdded: listener },
      action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
      alarms: { create: noop, onAlarm: listener },
      tabs: {
        create: noop, remove: noop, onUpdated: listener,
        query: async (q) => {
          seen.queried = q;
          if (queryThrows) throw new Error("no tabs permission");
          return tabs;
        },
      },
      notifications: { create: noop },
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "background.js" });
  return { context, seen };
}

const OPEN = [
  { id: 11, url: "https://www.youtube.com/watch?v=x" },
  { id: 12, url: "https://vimeo.com/1" },
];

console.log("\n-- turning it on reaches the tabs already open -------------------");
{
  const { context, seen } = world({ tabs: OPEN });
  check("syncInPageButton is reachable from the real file",
        typeof context.syncInPageButton === "function");
  await context.syncInPageButton();

  check("it registers for pages loaded from now on", seen.registered.length === 1);
  check("⭐ and injects into the tabs that are ALREADY open",
        seen.injected.length === 2, JSON.stringify(seen.injected));
  check("...each by its own tab id",
        seen.injected.includes(11) && seen.injected.includes(12),
        JSON.stringify(seen.injected));
  check("it asks only for tabs the button is allowed on",
        seen.queried && Array.isArray(seen.queried.url),
        JSON.stringify(seen.queried));
}

console.log("\n-- turning it off injects nothing --------------------------------");
{
  const { context, seen } = world({ inPageButton: false, tabs: OPEN });
  await context.syncInPageButton();
  check("nothing is registered", seen.registered.length === 0);
  check("⭐ and nothing is injected into open tabs", seen.injected.length === 0,
        JSON.stringify(seen.injected));
}

console.log("\n-- without the page permission, nothing happens ------------------");
{
  const { context, seen } = world({ granted: false, tabs: OPEN });
  await context.syncInPageButton();
  check("no registration without permission", seen.registered.length === 0);
  check("⭐ and no injection either - permission is the gate, not the toggle",
        seen.injected.length === 0, JSON.stringify(seen.injected));
}

console.log("\n-- a browser that will not answer, or will not script ------------");
{
  const { context, seen } = world({ tabs: OPEN, queryThrows: true });
  let threw = false;
  try { await context.syncInPageButton(); } catch { threw = true; }
  check("⭐ a refused tab query does not take the registration down with it",
        !threw && seen.registered.length === 1);
  check("...it simply injects into nothing", seen.injected.length === 0);
}
{
  const { context, seen } = world({ tabs: OPEN, scriptThrows: true });
  let threw = false;
  try { await context.syncInPageButton(); } catch { threw = true; }
  check("⭐ a page that cannot be scripted (chrome://, the store) is skipped quietly",
        !threw, "threw: " + threw);
  check("...and the registration still stands", seen.registered.length === 1);
}

console.log("\n-- a tab with no id ---------------------------------------------");
{
  const { context, seen } = world({ tabs: [{ url: "https://x/" }, { id: 9, url: "https://y/" }] });
  await context.syncInPageButton();
  check("it is skipped rather than injected as undefined",
        seen.injected.length === 1 && seen.injected[0] === 9,
        JSON.stringify(seen.injected));
}

console.log("\n" + "=".repeat(68));
console.log("  " + passed + " passed, " + failures.length + " failed");
for (const name of failures) console.log("   FAILED: " + name);
console.log("=".repeat(68) + "\n");
process.exit(failures.length ? 1 : 0);
