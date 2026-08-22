/*
 * A link that was handed over and never confirmed must not disappear when the
 * next one arrives.
 *
 * That promise is the whole reason ack=1 exists, and /done's own comment makes
 * it in writing: "Anything not named stays in flight and comes back on the next
 * /wait." The socket path keeps it - pushToSockets() concatenates the queue
 * onto whatever is still in flight. The long-poll path did not: it assigned
 *
 *     this.flight = this.queue;
 *
 * which throws away everything still waiting to be confirmed the moment a
 * second message shows up. So:
 *
 *   1. a link arrives and is handed to the PC
 *   2. the PC receives it but fails to queue it, so it does not confirm
 *   3. a second link arrives
 *   4. the first link is gone, for good, with nothing in any log
 *
 * This is the same defect that was already found and fixed in pushToSockets()
 * - the fix went into one of the two paths and never into the other.
 *
 * Runs on plain Node, no wrangler and no network.
 */
import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const relay = join(here, "..", "relay");

/* Two things the Workers runtime has and Node does not. Installed before the
 * module is imported, because it reaches for both by global name at call time.
 *
 * Node's Response refuses status 101 outright (RangeError), so it is wrapped
 * rather than replaced - everything else about it stays real, including json(). */
const RealResponse = globalThis.Response;
globalThis.Response = class extends RealResponse {
  constructor(body, init = {}) {
    const { webSocket, status, ...rest } = init;
    if (status === 101) super(null, { ...rest, status: 200 });
    else super(body, init);
    this._is101 = status === 101;
    this.webSocket = webSocket;
  }
  get status() { return this._is101 ? 101 : super.status; }
};
globalThis.WebSocketPair = function () {
  const server = {
    sent: [],
    send(d) { this.sent.push(d); },
    close() { this.closed = true; },
  };
  return { 0: { peer: server }, 1: server };
};

const shim = join(relay, ".flight-under-test-" + process.pid + ".mjs");
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

const msg = (n) => JSON.stringify({ n, c: "Zm9vYmFyYmF6cXV4" });
const settle = () => new Promise((r) => setImmediate(r));

/* A room that has been watched (so /send is allowed) and is already holding
 * one unconfirmed message. */
async function roomHoldingOne() {
  const state = makeState({
    seen: Date.now(),
    flight: [{ n: "first0000001", c: "Zm9vYmFyYmF6cXV4", at: Date.now() }],
    queue: [],
  });
  const room = new Room(state);
  await settle();
  return { room, state };
}

const poll = (room, extra = "&ack=1") =>
  room.fetch(new Request("https://room/poll?hold=1" + extra));

/* A poll that should have answered outright must not come back as a socket.
 * Reading .json() off a 101 throws, and a thrown test tells you far less than
 * a named failure does - especially during a mutation run, where the whole
 * point is to see WHICH assertion notices. */
async function bodyOf(answer, name) {
  if (answer.status !== 200) {
    check(name, false, "expected JSON, got status " + answer.status);
    return { msgs: [], held: 0 };
  }
  return answer.json();
}

console.log("\n-- an unconfirmed link survives a newer one -------------------");
{
  const { room } = await roomHoldingOne();

  // A second link arrives while the first is still unconfirmed.
  await room.fetch(new Request("https://room/send", { method: "POST", body: msg("second000002") }));

  const body = await bodyOf(await poll(room), "poll answered with JSON");
  const names = (body.msgs || []).map((m) => m.n);

  check("the new link is delivered", names.includes("second000002"), JSON.stringify(names));
  check("the UNCONFIRMED link is still there", names.includes("first0000001"),
        "got " + JSON.stringify(names) + " - the first link was dropped");
  check("held counts both", body.held === 2, "held=" + body.held);
}

console.log("\n-- and again on the next poll, until it is confirmed ----------");
{
  const { room } = await roomHoldingOne();
  await room.fetch(new Request("https://room/send", { method: "POST", body: msg("second000002") }));
  await poll(room);

  const body = await bodyOf(await poll(room), "poll answered with JSON");
  const names = (body.msgs || []).map((m) => m.n);
  check("both still waiting after a second poll", names.length === 2, JSON.stringify(names));
}

console.log("\n-- /done clears only what it names ----------------------------");
{
  const { room } = await roomHoldingOne();
  await room.fetch(new Request("https://room/send", { method: "POST", body: msg("second000002") }));
  await poll(room);

  // HTTP /done names them under "n". (The socket route uses "done" - two
  // spellings for the same promise, which is worth knowing before writing a
  // test that quietly gets a 400 and proves nothing.)
  const cleared = await room.fetch(new Request("https://room/done", {
    method: "POST", body: JSON.stringify({ n: ["second000002"] }),
  }));
  check("/done accepted the confirmation", cleared.status === 200, "status " + cleared.status);

  const body = await bodyOf(await poll(room), "poll answered with JSON");
  const names = (body.msgs || []).map((m) => m.n);
  check("the confirmed one is gone", !names.includes("second000002"), JSON.stringify(names));
  check("the unconfirmed one remains", names.includes("first0000001"), JSON.stringify(names));
}

console.log("\n-- a client that cannot ack keeps the old behaviour -----------");
{
  /* No ack=1: an older copy has no way to confirm, so the arrival of its next
   * poll is taken as proof the last batch landed. Changing that would leave
   * every existing install redelivering the same link forever.
   *
   * With a new message also waiting, the answer must contain that one and only
   * that one - which is the difference between the legacy branch still working
   * and the concat above quietly resurrecting old messages for old clients. */
  const { room } = await roomHoldingOne();
  await room.fetch(new Request("https://room/send", { method: "POST", body: msg("second000002") }));

  const body = await bodyOf(await poll(room, ""), "legacy poll answered with JSON");
  const names = (body.msgs || []).map((m) => m.n);
  check("legacy client's old batch is cleared", !names.includes("first0000001"), JSON.stringify(names));
  check("legacy client still gets the new one", names.includes("second000002"), JSON.stringify(names));
  check("and nothing else", names.length === 1, JSON.stringify(names));
}

console.log("\n-- legacy client with nothing new gets a socket, not a stale batch -");
{
  const state = makeState({
    seen: Date.now(),
    flight: [{ n: "first0000001", c: "Zm9vYmFyYmF6cXV4", at: Date.now() }],
    queue: [],
  });
  const room = new Room(state);
  await settle();

  const answer = await room.fetch(new Request("https://room/poll?hold=1"));
  check("nothing left to hand over, so a socket", answer.status === 101, "status " + answer.status);
}

console.log("\n-- with nothing waiting it hands over a socket, and never holds -");
{
  const state = makeState({ seen: Date.now(), flight: [], queue: [] });
  let accepted = null;
  state.acceptWebSocket = (ws) => { accepted = ws; };
  const room = new Room(state);
  await settle();

  /* The load-bearing property of this whole change: asking for a 5-second hold
   * must come back at once. A Durable Object cannot hibernate while a request
   * is still being processed, so "it answered immediately" is the same
   * statement as "this room is free to sleep". */
  const started = Date.now();
  const answer = await room.fetch(new Request("https://room/poll?hold=5&ack=1"));
  const took = Date.now() - started;

  check("answers 101, not JSON", answer.status === 101, "status " + answer.status);
  check("it never held the request", took < 250, took + "ms for a 5s hold");
  check("a socket was accepted", accepted !== null);
  check("no rows written for an empty poll",
        state.writes.filter((w) => w !== "seen").length === 0,
        JSON.stringify(state.writes));
}

console.log("\n-- polling is what makes a room real -------------------------");
{
  /* /send refuses a room no PC has ever watched - that guard is what stops
   * anyone filling the storage budget by posting to invented room ids. The
   * only thing that marks a room as watched is a PC asking for something, so
   * if /poll ever stopped calling markSeen(), every long-poll user's phone
   * would start being told "Your PC did not recognise this phone. Pair it
   * again." while nothing was actually wrong.
   *
   * A mutation run found this untested: markSeen() could be deleted outright
   * and every other assertion here still passed. */
  const state = makeState();                       // nothing stored: never seen
  const room = new Room(state);
  await settle();

  const refused = await room.fetch(new Request("https://room/send", { method: "POST", body: msg("before000001") }));
  check("an unwatched room refuses a send", refused.status === 404, "status " + refused.status);

  await room.fetch(new Request("https://room/poll?hold=1&ack=1"));

  const allowed = await room.fetch(new Request("https://room/send", { method: "POST", body: msg("after0000002") }));
  check("after a poll, the same send is accepted", allowed.status === 200, "status " + allowed.status);
  check("and the room was written down as seen", state.writes.includes("seen"),
        JSON.stringify(state.writes));
}

console.log("\n-- a link arriving after the socket is up still goes out ------");
{
  // The window between "nothing is waiting" and "here is a socket". /send has
  // to reach the socket that /poll just handed over, or the link waits 25s.
  const state = makeState({ seen: Date.now(), flight: [], queue: [] });
  const live = [];
  state.acceptWebSocket = (ws) => { live.push(ws); };
  state.getWebSockets = () => live;
  const room = new Room(state);
  await settle();

  await room.fetch(new Request("https://room/poll?hold=5&ack=1"));
  await room.fetch(new Request("https://room/send", { method: "POST", body: msg("late00000003") }));
  await settle();

  check("the socket received it", live.length === 1 && live[0].sent.length === 1,
        JSON.stringify(live.map((s) => s.sent)));
  const pushed = JSON.parse(live[0].sent[0] || "{}");
  check("and it is the right link",
        (pushed.msgs || []).some((m) => m.n === "late00000003"),
        JSON.stringify(pushed));
}

console.log("\n" + (failures.length ? "FAILED: " + failures.join(", ") : "all good"));
console.log(passed + " passed, " + failures.length + " failed\n");
process.exit(failures.length ? 1 : 0);
