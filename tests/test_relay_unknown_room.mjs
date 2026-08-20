/*
 * A room nobody has ever watched must not be allowed to write anything.
 *
 * The router checks that an address looks like a room id, not that it belongs
 * to anyone - so anybody could post to invented room ids and have that many
 * rooms writing to storage. On the free plan the daily budget for rows written
 * is what the relay runs on, and spending it stops the relay for everyone.
 *
 * Runs on plain Node, no wrangler and no network: the Room class is imported
 * directly and given a stand-in for the Durable Object state, which also lets
 * this count the writes rather than believe a comment about them.
 */
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const relay = join(here, "..", "relay");

// Copied to .mjs so Node reads it as a module wherever this repo sits - and
// kept beside the original, because worker.js imports its neighbours and a
// copy in a temp directory cannot find them.
const shim = join(relay, ".worker-under-test-" + process.pid + ".mjs");
writeFileSync(shim, readFileSync(join(relay, "worker.js")));
let Room;
try {
  ({ Room } = await import(pathToFileURL(shim).href));
} finally {
  unlinkSync(shim);
}

let passed = 0;
const failures = [];
function check(name, ok, detail = "") {
  if (ok) { passed++; console.log("  PASS  " + name); }
  else { failures.push(name); console.log("  FAIL  " + name + (detail ? " | " + detail : "")); }
}

/* The smallest state a Room needs, plus a tally of what it wrote. */
function makeState(stored = {}) {
  const data = new Map(Object.entries(stored));
  const writes = [];
  return {
    writes,
    blockConcurrencyWhile: async (fn) => { await fn(); },
    storage: {
      get: async (k) => data.get(k),
      put: async (k, v) => {
        if (typeof k === "object") { writes.push(...Object.keys(k)); for (const [a, b] of Object.entries(k)) data.set(a, b); }
        else { writes.push(k); data.set(k, v); }
      },
      delete: async (k) => { data.delete(k); },
      list: async () => new Map(data),
      deleteAll: async () => { data.clear(); },
    },
    getWebSockets: () => [],
    acceptWebSocket: () => {},
    setAlarm: async () => {},
  };
}

const envelope = JSON.stringify({ n: "abcd1234efgh", c: "Zm9vYmFyYmF6cXV4" });
const send = (room) =>
  room.fetch(new Request("https://room/send", { method: "POST", body: envelope }));

console.log("\n-- a room no PC has ever watched -----------------------------");
{
  const state = makeState();                     // no "seen" stored
  const room = new Room(state);
  await new Promise((r) => setImmediate(r));     // let the constructor settle
  const res = await send(room);
  const body = await res.json();
  check("it is refused", res.status === 404, "status " + res.status);
  check("and says so plainly", body.unknown === true, JSON.stringify(body));
  check("NOTHING was written to storage", state.writes.length === 0,
        "wrote: " + JSON.stringify(state.writes));
}

console.log("\n-- a room a PC has watched before ----------------------------");
{
  const state = makeState({ seen: Date.now(), queue: [], flight: [] });
  const room = new Room(state);
  await new Promise((r) => setImmediate(r));
  const res = await send(room);
  const body = await res.json();
  check("the message is accepted", res.status === 200, "status " + res.status);
  check("and queued", body.ok === true && body.queued >= 1, JSON.stringify(body));
  check("which does write to storage", state.writes.length > 0,
        "wrote: " + JSON.stringify(state.writes));
}

console.log("\n-- a PC that watched long ago still works --------------------");
{
  // Well past PC_QUIET: the PC is offline, but this is still its room. A
  // message left for a PC that is merely asleep is the whole point of a
  // postbox, so this must not be mistaken for an invented room.
  const state = makeState({ seen: Date.now() - 40 * 24 * 3600 * 1000, queue: [], flight: [] });
  const room = new Room(state);
  await new Promise((r) => setImmediate(r));
  const res = await send(room);
  check("still accepted", res.status === 200, "status " + res.status);
}

console.log("\n-- the flood, counted ----------------------------------------");
{
  let writes = 0;
  for (let i = 0; i < 500; i++) {
    const state = makeState();                   // each one an invented room
    const room = new Room(state);
    await new Promise((r) => setImmediate(r));
    await send(room);
    writes += state.writes.length;
  }
  check("500 invented rooms wrote 0 rows between them", writes === 0,
        "rows written: " + writes);
}

console.log("\n" + "=".repeat(62));
console.log("  " + passed + " passed, " + failures.length + " failed");
console.log("=".repeat(62));
process.exit(failures.length ? 1 : 0);
