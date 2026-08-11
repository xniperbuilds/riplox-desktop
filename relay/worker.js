/**
 * Riplox relay.
 *
 * A postbox, not a pipe. It carries a sealed envelope from a paired phone to
 * one PC and nothing else: no video ever passes through here, and what does
 * pass through is AES-GCM ciphertext this worker has no key for.
 *
 *   POST /send/:room   leave a sealed envelope
 *   GET  /wait/:room   the PC's held-open request, answered the moment one lands
 *   POST /ack/:room    the PC's sealed verdict on a message it just handled
 *   GET  /ack/:room    the phone waiting to hear that verdict
 *   GET  /p/:room      the page a phone uses to send, which does the crypto
 *
 * A Durable Object per room gives the held-open request something to wait on.
 * Long-poll rather than a WebSocket on purpose: the desktop app then needs no
 * websocket library at all, and both give the same two properties that matter
 * - the PC opens no port, and delivery is immediate rather than on a tick.
 */

import { ICON_192, ICON_512 } from "./icons.js";
import { APK_B64, APK_SIZE, APK_SHA256, APK_VERSION, APK_CODE } from "./apk.js";

const MAX_BODY = 4096;             // an envelope is a few hundred bytes
const MAX_HOLD = 30;               // seconds a /wait request is held open
const KEEP = 7 * 24 * 60 * 60 * 1000;
const MAX_QUEUE = 100;
const ACK_KEEP = 3 * 60 * 1000;    // a verdict nobody collected
const MAX_ACKS = 200;

const ROOM_RE = /^[a-f0-9]{16,64}$/;
const RID_RE = /^[A-Za-z0-9_-]{6,40}$/;

const APP_PAGE = "https://xniperbuilds.com/riplox-desktop/";

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "content-type",
      "cache-control": "no-store",
    },
  });

function bytes(b64) {
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function png(b64) {
  return new Response(bytes(b64), {
    headers: {
      "content-type": "image/png",
      "cache-control": "public, max-age=604800",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);

    if (request.method === "OPTIONS") return json({ ok: true });

    if (parts.length === 0) {
      return new Response(LANDING, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    // The phone's page. "share" is where an Android share sheet and an iOS
    // Shortcut both land; the room and the key come from this origin's own
    // storage, because a share sheet cannot carry a URL fragment.
    if (parts[0] === "p") {
      return new Response(PAGE, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    // An installed app is the only thing Android offers in a share sheet, and
    // Chrome only installs a page whose manifest names an icon. These three
    // routes are the whole reason "Share -> Riplox" exists.
    if (parts[0] === "manifest.webmanifest") {
      return new Response(JSON.stringify(MANIFEST), {
        headers: {
          "content-type": "application/manifest+json; charset=utf-8",
          "cache-control": "public, max-age=3600",
        },
      });
    }
    if (parts[0] === "icon-192.png") return png(ICON_192);
    if (parts[0] === "icon-512.png") return png(ICON_512);

    // The phone app. Served from here because this is already the one address
    // a phone gets sent to - a second place to fetch it from is a second
    // place that can fall out of step with the pairing it belongs to.
    if (parts[0] === "app.apk") {
      return new Response(bytes(APK_B64), {
        headers: {
          "content-type": "application/vnd.android.package-archive",
          "content-disposition": 'attachment; filename="RiploxSend.apk"',
          "cache-control": "public, max-age=3600",
        },
      });
    }
    // What the installed app asks before offering an update. The version code
    // is the one that decides - a name like "1.0.1" is for people to read,
    // and comparing those is guesswork the moment one gains a suffix.
    if (parts[0] === "app.json") {
      return json({
        version: APK_VERSION,
        code: APK_CODE,
        size: APK_SIZE,
        sha256: APK_SHA256,
        url: "/app.apk",
      });
    }
    if (parts[0] === "sw.js") {
      return new Response(SERVICE_WORKER, {
        headers: {
          "content-type": "text/javascript; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    // The phone stamps every message with a time, and the PC refuses anything
    // sent from the future. A phone with a drifted clock would be told "sent"
    // over a message that was thrown away - so it takes the time from here
    // rather than trusting its own.
    if (parts[0] === "now") {
      return json({ now: Date.now() / 1000 });
    }

    const room = parts[1];
    if (!room || !ROOM_RE.test(room)) return json({ ok: false }, 400);

    const stub = env.ROOMS.get(env.ROOMS.idFromName(room));

    if (parts[0] === "send" && request.method === "POST") {
      const raw = await request.text();
      if (raw.length > MAX_BODY) return json({ ok: false }, 413);
      return stub.fetch("https://room/send", { method: "POST", body: raw });
    }

    if (parts[0] === "wait" && request.method === "GET") {
      const hold = url.searchParams.get("hold") || "25";
      return stub.fetch(`https://room/wait?hold=${hold}`);
    }

    if (parts[0] === "ack") {
      if (request.method === "POST") {
        const raw = await request.text();
        if (raw.length > MAX_BODY) return json({ ok: false }, 413);
        return stub.fetch("https://room/ack", { method: "POST", body: raw });
      }
      const rid = url.searchParams.get("r") || "";
      const hold = url.searchParams.get("hold") || "10";
      return stub.fetch(
        `https://room/ack?r=${encodeURIComponent(rid)}&hold=${hold}`
      );
    }

    return json({ ok: false }, 404);
  },
};

/**
 * One room, one PC reading it.
 *
 * There is deliberately no cursor. A Durable Object is evicted when it goes
 * idle and comes back with its counter at zero, so a PC remembering "I have
 * had up to number 5" would silently ignore everything after a restart -
 * every message lost, for good, with nothing in any log. That was found by
 * sending a real message through a real deployment, not by reading the code.
 *
 * Instead the reader is handed whatever is waiting, and that batch is dropped
 * only when the *next* request arrives - which is proof the last response was
 * received. A redelivery is harmless: the PC refuses a repeated nonce anyway.
 *
 * The queue itself is on disk, not in memory, and that is the difference
 * between "your PC was off, so the link is gone" and "your PC was off, so the
 * link is waiting". Eviction used to take the whole queue with it.
 */
export class Room {
  constructor(state) {
    this.state = state;
    this.queue = [];         // {n, c, at} waiting to be handed over
    this.flight = [];        // handed over, not yet confirmed
    this.waiters = [];       // resolve functions of held-open /wait requests
    this.acks = new Map();   // rid -> {n, c, at}
    this.ackWaiters = new Map();

    state.blockConcurrencyWhile(async () => {
      this.queue = (await state.storage.get("queue")) || [];
      this.flight = (await state.storage.get("flight")) || [];
    });
  }

  async keep() {
    await this.state.storage.put({ queue: this.queue, flight: this.flight });
  }

  sweep() {
    const cutoff = Date.now() - KEEP;
    this.queue = this.queue.filter((m) => m.at > cutoff).slice(-MAX_QUEUE);

    const stale = Date.now() - ACK_KEEP;
    for (const [rid, ack] of this.acks) {
      if (ack.at < stale) this.acks.delete(rid);
    }
    // A verdict lives for seconds and is collected by whoever asked for it.
    // Anyone who knows the room could still post thousands of them under
    // invented ids, so the map is bounded rather than trusted: oldest out.
    while (this.acks.size > MAX_ACKS) {
      this.acks.delete(this.acks.keys().next().value);
    }
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/send") {
      let body;
      try {
        body = JSON.parse(await request.text());
      } catch {
        return json({ ok: false }, 400);
      }
      const n = String(body.n || "");
      const c = String(body.c || "");
      // Base64url, and nothing else - this worker cannot read the contents,
      // so shape is the only thing it can check.
      if (!/^[A-Za-z0-9_-]{8,64}$/.test(n) || !/^[A-Za-z0-9_-]{8,4000}$/.test(c)) {
        return json({ ok: false }, 400);
      }

      this.sweep();
      this.queue.push({ n, c, at: Date.now() });
      await this.keep();

      const waiting = this.waiters;
      this.waiters = [];
      waiting.forEach((resolve) => resolve());

      return json({ ok: true, queued: this.queue.length });
    }

    if (url.pathname === "/wait") {
      const hold = Math.min(Number(url.searchParams.get("hold") || 25) || 25, MAX_HOLD);

      // This request arriving is proof the previous response got through.
      if (this.flight.length) {
        this.flight = [];
        await this.keep();
      }
      this.sweep();

      if (this.queue.length === 0) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, hold * 1000);
          this.waiters.push(() => {
            clearTimeout(timer);
            resolve();
          });
        });
        this.sweep();
      }

      if (this.queue.length) {
        this.flight = this.queue;
        this.queue = [];
        await this.keep();
      }

      return json({
        ok: true,
        msgs: this.flight.map((m) => ({ n: m.n, c: m.c })),
      });
    }

    /* The verdict, going the other way.
     *
     * Held in memory rather than on disk on purpose: this is a reply to a
     * request the phone is holding open right now, measured in seconds. A
     * verdict nobody is waiting for has nowhere useful to go, and writing it
     * down would only keep something the phone will never ask for again. */
    if (url.pathname === "/ack" && request.method === "POST") {
      let body;
      try {
        body = JSON.parse(await request.text());
      } catch {
        return json({ ok: false }, 400);
      }
      const rid = String(body.r || "");
      if (!RID_RE.test(rid)) return json({ ok: false }, 400);

      this.sweep();
      this.acks.set(rid, {
        n: String(body.n || ""),
        c: String(body.c || ""),
        at: Date.now(),
      });

      const waiting = this.ackWaiters.get(rid) || [];
      this.ackWaiters.delete(rid);
      waiting.forEach((resolve) => resolve());

      return json({ ok: true });
    }

    if (url.pathname === "/ack") {
      const rid = url.searchParams.get("r") || "";
      if (!RID_RE.test(rid)) return json({ ok: false }, 400);
      const hold = Math.min(Number(url.searchParams.get("hold") || 10) || 10, MAX_HOLD);

      if (!this.acks.has(rid)) {
        await new Promise((resolve) => {
          const timer = setTimeout(resolve, hold * 1000);
          const list = this.ackWaiters.get(rid) || [];
          list.push(() => {
            clearTimeout(timer);
            resolve();
          });
          this.ackWaiters.set(rid, list);
        });
      }

      const ack = this.acks.get(rid);
      if (!ack) return json({ ok: false });
      this.acks.delete(rid);
      return json({ ok: true, ack: { n: ack.n, c: ack.c } });
    }

    return json({ ok: false }, 404);
  }
}

const MANIFEST = {
  id: "/p/",
  name: "Send to Riplox",
  short_name: "Riplox",
  description: "Share a link from your phone and your PC downloads it.",
  start_url: "/p/",
  scope: "/",
  display: "standalone",
  background_color: "#070C15",
  theme_color: "#070C15",
  icons: [
    { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
    { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
    { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
  ],
  // Android only. iOS has no share target of any kind, which is why the
  // iPhone route is a Shortcut that opens this same page instead.
  share_target: {
    action: "/p/share",
    method: "GET",
    params: { title: "title", text: "text", url: "url" },
  },
};

/* Installability has one more condition than a manifest: a service worker
 * with a fetch handler. It caches nothing - everything here is either a live
 * message or a page that must never be stale - it exists so the browser will
 * treat this as an app, which is the only way into the share sheet. */
const SERVICE_WORKER = `self.addEventListener("install", function () {
  self.skipWaiting();
});
self.addEventListener("activate", function (e) {
  e.waitUntil(self.clients.claim());
});
self.addEventListener("fetch", function () {});
`;

const LANDING = `<!doctype html><meta charset="utf-8">
<title>Riplox relay</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:15px/1.6 system-ui;margin:0;padding:12vh 24px;background:#070C15;color:#93A2B8}
h1{color:#E9EFF8;font-size:22px;margin:0 0 10px}code{color:#2BE9E0}
a{color:#2BE9E0}</style>
<h1>Riplox relay</h1>
<p>A postbox for the Riplox desktop app. It carries a short encrypted message
from a paired phone to one PC, cannot read what it carries, and no video passes
through it.</p>
<p><a href="/p/">Pair a device</a> &middot; <a href="${APP_PAGE}">Get Riplox for Windows</a></p>`;

const PAGE = String.raw`<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Send to Riplox</title>
<meta name="theme-color" content="#070C15">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;padding:26px 20px 40px;background:#070C15;
       color:#E9EFF8;font:16px/1.55 system-ui,-apple-system,sans-serif}
  .wrap{max-width:460px;margin:0 auto}
  .brand{display:flex;align-items:center;gap:11px;margin:0 0 22px}
  .brand img{width:34px;height:34px;border-radius:9px}
  h1{font-size:19px;margin:0;letter-spacing:.01em}
  label{display:block;font-size:12px;letter-spacing:.12em;text-transform:uppercase;
        color:#7E8DA6;margin:18px 0 7px}
  input,select,button{font:inherit;width:100%;border-radius:11px}
  input,select{padding:13px 14px;border:1px solid rgba(255,255,255,.12);
        background:#0D1421;color:#E9EFF8}
  input:focus,select:focus{outline:none;border-color:#2BE9E0}
  button{margin-top:20px;padding:15px;border:0;font-weight:600;
        background:linear-gradient(135deg,#2BE9E0,#17C3D6);color:#04222B}
  button:disabled{opacity:.5}
  button.quiet{background:none;border:1px solid rgba(255,255,255,.14);
        color:#E9EFF8;font-weight:500;padding:13px;margin-top:12px}
  #chooseCard h2{font-size:16px}
  .msg{margin-top:18px;padding:12px 14px;border-radius:11px;font-size:14px;display:none}
  .msg.ok{display:block;background:rgba(43,233,224,.1);border:1px solid rgba(43,233,224,.35);color:#9BF6F1}
  .msg.bad{display:block;background:rgba(255,61,110,.1);border:1px solid rgba(255,61,110,.35);color:#FFC5D4}
  .msg.wait{display:block;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);color:#93A2B8}
  .card{margin-top:22px;padding:18px;border-radius:14px;
        border:1px solid rgba(255,255,255,.1);background:#0B1220}
  .card h2{font-size:15px;margin:0 0 6px}
  .card p{margin:0;font-size:13.5px;color:#93A2B8}
  .card a{color:#2BE9E0;text-decoration:none;font-weight:600}
  .card .tiny{margin-top:10px;font-size:12px;color:#7E8DA6}
  a.btn{display:block;margin-top:14px;padding:15px;border-radius:11px;
        text-align:center;font-weight:600;color:#04222B;
        background:linear-gradient(135deg,#2BE9E0,#17C3D6)}
  .foot{margin-top:24px;font-size:12px;color:#7E8DA6}
  #paired,#unpaired{display:none}
</style>
</head><body>
<div class="wrap">

<div class="brand">
  <img src="/icon-192.png" alt="">
  <h1>Send to Riplox</h1>
</div>

<!-- Shown when a fresh pairing code arrives on a phone that could use the app.
     The page deliberately does not pair itself here: a code works once, so
     pairing the browser left the app with nothing and a second code had to be
     generated for it. -->
<div class="card" id="chooseCard" style="display:none;margin-top:0">
  <h2 id="chooseHead">Pair the app, or this browser?</h2>
  <p id="chooseWhy">The app takes a share without opening anything. This browser
     works too, but sharing opens this page for a moment — and <b>the code works
     once</b>, so it pairs whichever you pick.</p>
  <a class="btn" id="toApp">Pair the Riplox Send app</a>
  <button class="quiet" id="toBrowser">Pair this browser instead</button>
  <p class="tiny" id="chooseNote">No app yet? The button installs it, then come
     back to this page and tap it again.</p>
</div>

<div class="card" id="appCard" style="display:none;margin-top:0">
  <h2>Get the app — one tap, nothing opens</h2>
  <p>Riplox Send adds itself to the share sheet. Share a video from any app and
     it goes straight to your PC — <b>no window, no page, just a tick</b>.
     ${Math.round(APK_SIZE / 1024)} KB.</p>
  <a class="btn" href="/app.apk" id="getApp">Download Riplox Send</a>
  <p class="tiny" id="appNote">Android will ask permission to install it — that
     is normal for an app that does not come from the Play Store.</p>
</div>

<div class="card" id="installCard" style="display:none;margin-top:22px">
  <h2 id="installHead">Or add this page to your home screen</h2>
  <p id="installWhy">Works without installing anything, but sharing opens this
     page for a moment.</p>
  <button id="install">Add to home screen</button>
</div>

<div id="paired">
  <label for="url">Link</label>
  <input id="url" type="url" inputmode="url" autocomplete="off"
         placeholder="https://..." enterkeyhint="send">

  <label for="q">Quality</label>
  <select id="q">
    <option value="">PC's setting</option>
    <option value="best">Best available</option>
    <option value="1080">1080p</option>
    <option value="720">720p</option>
    <option value="480">480p</option>
    <option value="mp3">Audio only</option>
  </select>

  <button id="go">Send</button>
</div>

<div id="unpaired">
  <div class="card">
    <h2>Pair this phone</h2>
    <p>Open Riplox on your PC &rarr; Sharing &rarr; Show code. Scan the QR, or
       paste the code below.</p>
    <input id="code" type="text" autocomplete="off" autocapitalize="none"
           spellcheck="false" placeholder="Paste pairing code" style="margin-top:12px">
    <button id="pair">Pair</button>
  </div>

  <div class="card">
    <h2>No Riplox on your PC yet?</h2>
    <p><a href="${APP_PAGE}">Get Riplox for Windows</a> &mdash; free, no account.
       Install it, turn on Sharing, then come back here.</p>
  </div>
</div>

<div class="msg" id="msg"></div>

<p class="foot" id="foot"></p>

</div>
<script>
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var KEY = "riplox.pair";

  if ("serviceWorker" in navigator) {
    // Not for caching. An installed app is the only thing Android puts in a
    // share sheet, and this is one of the conditions for being installable.
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  function b64e(buf) {
    var s = btoa(String.fromCharCode.apply(null, new Uint8Array(buf)));
    return s.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function b64d(text) {
    text = String(text).replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(text + "=".repeat((4 - text.length % 4) % 4));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function say(text, kind) {
    var box = $("msg");
    box.textContent = text;
    box.className = "msg " + (kind || "bad");
  }

  /* ------------------------------------------------------------- pairing */

  var pair = null;
  try { pair = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) {}

  // Three ways the same three values can arrive: a scanned link, the fragment
  // of one, or the code typed in by hand. All of them are read here, in the
  // browser - the key is never part of a request, so the relay never sees it.
  function readInvite(text) {
    text = String(text || "").trim();
    if (!text) return null;

    var room = "";
    var frag = text;
    var slash = text.indexOf("/p/");
    if (slash >= 0) {
      var rest = text.slice(slash + 3);
      room = rest.split(/[#?/]/)[0] || "";
      var hash = rest.indexOf("#");
      frag = hash >= 0 ? rest.slice(hash + 1) : "";
    }

    if (frag.indexOf("k=") >= 0) {
      var q = new URLSearchParams(frag);
      if (!q.get("k")) return null;
      // The PC's address on the local network is read but never used here: an
      // HTTPS page cannot call a plain-HTTP address on the LAN. It is carried
      // so the desktop app, which can, gets it when this link is handed over.
      return { room: room || currentRoom(), key: q.get("k"),
               code: q.get("c") || "", lan: q.get("l") || "" };
    }

    // The typed form: room.key.code, which carries everything, so it works
    // even on a phone that never opened the scanned link.
    var bits = text.split(".");
    if (bits.length === 3 && /^[a-f0-9]{16,64}$/.test(bits[0])) {
      return { room: bits[0], key: bits[1], code: bits[2] };
    }
    return null;
  }

  function currentRoom() {
    var seg = location.pathname.split("/")[2] || "";
    return /^[a-f0-9]{16,64}$/.test(seg) ? seg : (pair && pair.room) || "";
  }

  var fresh = readInvite(location.hash ? location.href : "");
  if (fresh && fresh.room) history.replaceState(null, "", "/p/" + fresh.room);

  function show() {
    $("paired").style.display = pair ? "block" : "none";
    $("unpaired").style.display = pair ? "none" : "block";
    $("foot").textContent = pair
      ? "Encrypted. Only a link travels - never a video."
      : "";
    // Offered whether or not this phone is paired yet: someone who has just
    // arrived should be able to get the app first and pair inside it.
    offerInstall();
  }

  /* ------------------------------------------------------------- install */

  /* Chrome fires this instead of installing, and hands over the prompt to
     raise later - so the page can offer its own button rather than leaving
     people to find "Install app" in the browser menu. Nothing appears if the
     app is already installed, because then the event never fires. */
  var waitingPrompt = null;
  var installed = (window.matchMedia
      && window.matchMedia("(display-mode: standalone)").matches)
      || navigator.standalone === true;

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    waitingPrompt = e;
    offerInstall();
  });

  window.addEventListener("appinstalled", function () {
    waitingPrompt = null;
    installed = true;
    $("installCard").style.display = "none";
  });

  function offerInstall() {
    var card = $("installCard");
    var app = $("appCard");
    var android = /Android/.test(navigator.userAgent);
    var apple = /iPhone|iPad|iPod/.test(navigator.userAgent);

    // The choice card owns the screen while it is up.
    if ($("chooseCard").style.display === "block") return;
    if (installed) { card.style.display = app.style.display = "none"; return; }

    // The app is the real answer on Android and the only one that can take a
    // share without opening anything, so it is offered first. The home-screen
    // route stays underneath for anyone who would rather install nothing.
    app.style.display = android ? "block" : "none";

    if (waitingPrompt) {
      card.style.display = "block";
      $("install").style.display = "block";
      return;
    }

    // Safari never fires the install event and has no install API at all, and
    // there is no iPhone build - so the only honest thing to show is where
    // Apple's own button is.
    if (apple) {
      card.style.display = "block";
      $("installHead").textContent = "Add Riplox to your Home Screen";
      $("installWhy").innerHTML = "In Safari: <b>Share &rarr; Add to Home "
        + "Screen</b>. iOS has no share target for web pages, so to share "
        + "in one tap you also need the Shortcut - your PC's Sharing screen "
        + "has the steps.";
      $("install").style.display = "none";
    }
  }

  $("install").addEventListener("click", function () {
    if (!waitingPrompt) return;
    waitingPrompt.prompt();
    waitingPrompt.userChoice.then(function (choice) {
      waitingPrompt = null;
      if (choice && choice.outcome === "accepted") {
        $("installCard").style.display = "none";
      }
    });
  });

  /* ------------------------------------------------------------- sending */

  var keyCache = {};
  function keyFor(raw) {
    if (!keyCache[raw]) {
      keyCache[raw] = crypto.subtle.importKey("raw", b64d(raw), "AES-GCM", false,
                                              ["encrypt", "decrypt"]);
    }
    return keyCache[raw];
  }

  // Offset against the relay's clock, so a phone whose time is wrong still
  // sends something the PC will accept.
  var skew = 0;
  var clock = fetch("/now").then(function (r) { return r.json(); })
    .then(function (j) { skew = j.now - Date.now() / 1000; })
    .catch(function () { skew = 0; });

  function stamp() { return Date.now() / 1000 + skew; }

  function rid() {
    return b64e(crypto.getRandomValues(new Uint8Array(12)));
  }

  function seal(rawKey, body) {
    var nonce = crypto.getRandomValues(new Uint8Array(12));
    var plain = new TextEncoder().encode(JSON.stringify(body));
    return keyFor(rawKey).then(function (key) {
      return crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, plain);
    }).then(function (cipher) {
      return { n: b64e(nonce), c: b64e(cipher) };
    });
  }

  function unseal(rawKey, envelope) {
    return keyFor(rawKey).then(function (key) {
      return crypto.subtle.decrypt(
        { name: "AES-GCM", iv: b64d(envelope.n) }, key, b64d(envelope.c));
    }).then(function (raw) {
      return JSON.parse(new TextDecoder().decode(raw));
    });
  }

  /* This page always goes through the relay, and that is not a shortcut. A
     page served over HTTPS is not allowed to call http://192.168.x.x at all -
     the browser blocks it as mixed content before any of our code runs. The
     PC's LAN listener is still there for a paired native app, which has no
     such restriction; from a web page it can never be reached. */
  function deliver(room, rawKey, body) {
    var id = rid();
    body.r = id;
    return seal(rawKey, body).then(function (envelope) {
      return fetch("/send/" + room, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(envelope),
      });
    }).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return id;
    });
  }

  /* The relay is a postbox: it can honestly say a message was left, and
     nothing more. The PC's own answer comes back sealed under the same key,
     which is the difference between "Sent" and knowing it was actually
     queued - or refused, and why. */
  function verdict(room, rawKey, id) {
    return fetch("/ack/" + room + "?r=" + id + "&hold=12")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.ok || !j.ack) return "";
        return unseal(rawKey, j.ack).then(function (body) { return body.why || ""; });
      })
      .catch(function () { return ""; });
  }

  var WORDS = {
    queued: ["Sent. Downloading on your PC now.", "ok"],
    held: ["Sent. Waiting for approval on your PC.", "ok"],
    paired: ["Paired. Send a link whenever you like.", "ok"],
    paused: ["This phone is paused on the PC. Resume it there.", "bad"],
    site: ["This phone is not allowed to send links from that site.", "bad"],
    "day-limit": ["This phone has used up today's downloads.", "bad"],
    "total-limit": ["This phone has used up its total allowance.", "bad"],
    "bad-link": ["That link was refused.", "bad"],
    replay: ["That one has already been sent.", "bad"],
    stale: ["Your phone's clock is too far off. Fix the time and try again.", "bad"],
    expired: ["That pairing code has expired. Make a new one in Riplox.", "bad"],
    used: ["That pairing code has already been used. Make a new one in Riplox.", "bad"],
    unknown: ["Your PC did not recognise this phone. Pair it again.", "bad"],
  };

  function report(why, fallback) {
    var hit = WORDS[why];
    if (hit) { say(hit[0], hit[1]); return why; }
    say(fallback, "wait");
    return "";
  }

  /* ------------------------------------------------------------- actions */

  function doPair(invite) {
    if (!invite || !invite.room || !invite.key) {
      say("That code does not look right. Copy it again from Riplox.");
      return;
    }

    // The phone brings its own key. From here on the pairing link opens
    // nothing, so a code left in a chat or a screenshot is not a way in.
    var own = b64e(crypto.getRandomValues(new Uint8Array(32)));

    say("Pairing...", "wait");
    clock.then(function () {
      return deliver(invite.room, invite.key, {
        kind: "hello", code: invite.code, key: own, name: guessName(),
        ts: stamp(),
      });
    }).then(function (id) {
      return verdict(invite.room, invite.key, id);
    }).then(function (why) {
      if (why === "paired") {
        pair = { room: invite.room, key: own };
        try { localStorage.setItem(KEY, JSON.stringify(pair)); } catch (e) {}
        show();
      }
      // Pairing is the one thing that cannot be left in a postbox: the PC has
      // to be running to record the device, and the code is only good for two
      // minutes. Silence here means not paired, so it must not read like "ok".
      report(why, "No answer from your PC. Open Riplox, turn Sharing on, and "
                + "use a fresh code.");
    }).catch(function () {
      say("Could not reach the relay. Check your connection.");
    });
  }

  function send(auto) {
    var text = ($("url").value || "").trim();
    var found = text.match(/https?:\/\/[^\s]+/);
    if (!found) { say("That does not look like a link."); return; }

    $("go").disabled = true;
    $("go").textContent = "Sending...";
    say("Sending...", "wait");

    clock.then(function () {
      return deliver(pair.room, pair.key,
                     { url: found[0], quality: $("q").value || "", ts: stamp() });
    }).then(function (id) {
      return verdict(pair.room, pair.key, id);
    }).then(function (why) {
      var good = (why === "queued" || why === "held" || !why);
      if (good) $("url").value = "";
      report(why, "Left for your PC. It starts as soon as Riplox is running.");
      if (auto) {
        document.title = "Sent to Riplox";
        if (good) leave();
      }
    }).catch(function () {
      say("Could not reach the relay. Check your connection.");
    }).then(function () {
      $("go").disabled = false;
      $("go").textContent = "Send";
    });
  }

  /* Sharing to a web app always opens it - there is no silent share target on
     the web, only in a native app. So the next best thing: send straight
     away, say so for a moment, then get out of the way.

     window.close() is allowed to refuse, since this window was opened by the
     browser rather than by script. If it does refuse, the page says the job
     is finished instead of sitting there looking half-done. */
  function leave() {
    setTimeout(function () {
      window.close();
      setTimeout(function () {
        $("go").textContent = "Sent - you can close this";
        $("go").disabled = true;
      }, 400);
    }, 1100);
  }

  function guessName() {
    var ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua)) return "iPad";
    if (/Android/.test(ua)) return "Android phone";
    return "Phone";
  }

  /* A fresh code on an Android phone is not paired on the spot any more.
     The code works once, so pairing the browser here spent it - and the app,
     which is the whole point on Android, was then left needing a second code
     generated on the PC. Ask which one it is for. */
  function offerChoice(invite) {
    var code = invite.room + "." + invite.key + "." + invite.code;
    var apk = location.origin + "/app.apk";

    // Chrome's intent syntax: opens the app if it is installed, and falls
    // through to the download if it is not. The code rides in the URL and
    // never reaches a server - this link is built and followed on the phone.
    $("toApp").href = "intent://pair?c=" + encodeURIComponent(code)
      + "#Intent;scheme=riploxsend;package=com.xniperbuilds.sendtoriplox;"
      + "S.browser_fallback_url=" + encodeURIComponent(apk) + ";end";

    showChoice(invite);
  }

  /* The same question on Windows, and for the same reason. Riplox Send
     registers riploxsend:// when it installs, so Windows hands the link to it
     rather than to this page - and the app is the only side that can reach the
     PC over the local network, which is the whole reason it exists. */
  function offerChoiceWindows(invite) {
    var code = invite.room + "." + invite.key + "." + invite.code;
    var link = "riploxsend://pair?c=" + encodeURIComponent(code);
    if (invite.lan) link += "&l=" + encodeURIComponent(invite.lan);

    $("chooseHead").textContent = "Pair the app, or this browser?";
    $("chooseWhy").innerHTML = "Riplox Send sits in the tray: copy a link in "
      + "any program and press its shortcut. This browser works too, but "
      + "<b>the code works once</b>, so it pairs whichever you pick.";
    $("toApp").textContent = "Pair the Riplox Send app";
    $("toApp").href = link;
    $("chooseNote").innerHTML = "Nothing happens? Riplox Send is not installed "
      + "on this PC - <a href=\"${APP_PAGE}\">get it for Windows</a>, then "
      + "open this link again.";

    showChoice(invite);
  }

  function showChoice(invite) {
    $("chooseCard").style.display = "block";
    $("appCard").style.display = "none";
    $("installCard").style.display = "none";

    $("toBrowser").addEventListener("click", function () {
      $("chooseCard").style.display = "none";
      doPair(invite);
    });
  }

  show();
  if (fresh) {
    var ua = navigator.userAgent;
    if (/Android/.test(ua)) offerChoice(fresh);
    // A phone is not a desktop: iOS has no app of ours to hand this to, so it
    // keeps pairing the page exactly as it did.
    else if (/Windows NT/.test(ua) && !/Android|iPhone|iPad/.test(ua)) offerChoiceWindows(fresh);
    else doPair(fresh);
  }

  $("pair").addEventListener("click", function () {
    doPair(readInvite($("code").value));
  });
  $("go").addEventListener("click", function () { send(false); });
  $("url").addEventListener("keydown", function (e) {
    if (e.key === "Enter") send(false);
  });

  // Where the Android share sheet and the iOS Shortcut both land.
  var params = new URLSearchParams(location.search);
  var shared = params.get("url") || params.get("text") || "";
  var arrived = shared.match(/https?:\/\/[^\s]+/);
  if (arrived && pair) {
    // Arriving from a share sheet is not a visit to this page - the link is
    // already chosen. Show the one line that matters, not a form.
    $("url").value = arrived[0];
    $("installCard").style.display = "none";
    $("appCard").style.display = "none";
    [].forEach.call(
      document.querySelectorAll("#paired > label, #paired > input, #paired > select"),
      function (el) { el.style.display = "none"; });
    send(true);
  } else if (arrived) {
    say("Pair this phone first, then share again.");
  }
})();
</script>
</body></html>`;
