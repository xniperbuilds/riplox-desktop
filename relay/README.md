# Riplox relay

A postbox for "Send to Riplox". It carries one short encrypted message from a
paired phone to one PC.

**It cannot read what it carries.** The message is AES-GCM ciphertext; the key
never leaves the two devices, and the phone receives it in a URL fragment,
which browsers do not send to servers. No video passes through here — only a
link, a quality and an optional trim.

Live at **https://relay.xniperbuilds.com**.

## Routes

| Route | Who calls it | What it does |
|---|---|---|
| `POST /send/:room` | the phone | leaves a sealed envelope |
| `GET /wait/:room?hold=` | the PC | held open until something arrives |
| `POST /ack/:room` | the PC | leaves its sealed verdict on a message |
| `GET /ack/:room?r=` | the phone | held open until that verdict lands |
| `GET /now` | the phone | the relay's clock, so a drifted phone still works |
| `GET /p/:room` | the phone | the page that does the encrypting |
| `GET /manifest.webmanifest` | Android | makes the page a share target |
| `GET /icon-192.png`, `/icon-512.png` | Android | without these it is not installable |
| `GET /sw.js` | the browser | caches nothing; installability needs one |

An envelope waits **seven days**, on disk. It used to live in the Durable
Object's memory and be dropped after five minutes, which meant a link sent
while the PC was switched off was gone before the PC ever came back — and gone
silently, because eviction takes the queue with it.

The verdict route exists because a postbox can only ever honestly say "left for
your PC". Without it the page showed "Sent" for a link that was refused as a
replay, or sent from a paused device, or sent with a pairing code that had
already been used. The PC seals its answer with the same key the message
arrived under, so only the sender can read it — and a stranger, whose message
could not be opened at all, is never replied to.

The PC never opens a port to the internet: it dials out and holds one request
open. That is a long poll rather than a WebSocket on purpose — the desktop app
then needs no websocket library, and both give the two properties that matter
(nothing can reach the PC, and delivery is immediate). Measured on the live
deployment: a message sent while a request was held open arrived in **0.44 s**.

## Three things that were found by running it, not by reading it

1. **No cursor.** A Durable Object is evicted when idle and comes back with
   its counters at zero. A reader remembering "I have had up to number 5"
   therefore ignores everything after a restart — silently, for ever. The
   reader is now handed whatever is waiting, and that batch is dropped only
   when the *next* request arrives, which proves the last response landed. A
   repeat is harmless: the PC refuses a nonce it has already seen.

2. **The web page cannot use the LAN.** A page served over HTTPS is forbidden
   by the browser from calling `http://192.168.x.x` at all — blocked as mixed
   content before any of our code runs. The PC's LAN listener is for a paired
   native app, which has no such restriction. The page says the relay carried
   it, because that is what happened.

3. **Say who you are.** Python's default `Python-urllib/3.x` user agent is
   refused by Cloudflare's bot rules with a 403, so the desktop app would
   never have connected once. It now sends a real `User-Agent`.

## Deploying

```
npx wrangler deploy
```

Authentication is a Cloudflare API token with **Workers Scripts: Edit**, set as
`CLOUDFLARE_API_TOKEN`, plus `CLOUDFLARE_ACCOUNT_ID`. A DNS/zone-only token is
not enough — it fails with `Authentication error [code: 10000]`.

The address is a **custom domain on the brand's own zone**, not the generated
`*.workers.dev` subdomain: that subdomain contains the account owner's email
address, and this address is printed into a QR code and shipped as the app's
default.

Durable Objects on the free plan must be SQLite-backed, which is what
`new_sqlite_classes` in `wrangler.toml` asks for. On the very first deploy the
namespace takes a minute or so to become callable — a `1101` from `/send`
straight after that first deploy is propagation, not a bug.

## Cost

Free-plan Workers allow 100,000 requests a day. One PC holding a 25-second
poll open uses about 3,500 a day, so a handful of machines fit comfortably.
An undelivered envelope is dropped after seven days and a room keeps at most
100 of them.
