# Tests

```bash
python tests/run.py            # everything, including the tests that go online
python tests/run.py --offline  # only the ones that need no network
python tests/run.py cover door # only files whose names contain these words
python tests/test_proxy.py     # any one of them, on its own
```

Each file is a plain script. It prints its checks, prints a tally, and exits
non-zero if anything failed. No test framework is needed and none is
installed: `python tests/run.py` works on a fresh clone with nothing but the
app's own requirements.

## The online ones are the point

Five files talk to real sites. They are slower and they need a connection, and
they are also the only tests here that mean anything: a door that passes
against a recorded answer proves nothing about the day the site changes its
page. `--offline` leaves them out and says so at the end rather than quietly
reporting a smaller run as a full one.

`test_relay_socket.py` needs the relay running locally and is held back from
both runs until it is:

```bash
cd relay && npx wrangler dev --port 8799 --local
```

## The Board's tests are JavaScript, and `run.py` never sees them

Two files here end in `.mjs`, because the thing they test is a Cloudflare
Worker. `python tests/run.py` does not know about them and will not tell you
they were skipped, so they are written down here instead:

```bash
node tests/test_board_parser.mjs          # no network, no Worker needed
cd board && npx wrangler dev --port 8798 --local
node tests/test_board_worker.mjs          # needs that Worker running
```

`test_board_parser.mjs` is the one that matters most. The Board's whole
security model is that a link is never trusted and never stored as typed - it
is parsed down to a platform and a content id and rebuilt from the Worker's
own table - so that file is an attack corpus, and it ends by breaking the
parser on purpose six times to check the corpus would notice.

`test_board_worker.mjs` runs the flow against a real `wrangler dev`: a link is
stored, comes back out of the feed, cannot be posted twice, stops being
postable after five in an hour, disappears at three reports from three
different places, comes back when restored, and stops entirely when the kill
switch is thrown. Every id is generated fresh per run, because the Durable
Object keeps its table between runs and a second run reusing an id would fail
for the wrong reason.

## What is not here

`tests-local/` is not in the repository. It holds one-off probes and
diagnostics, plus about a dozen tests that still carry real links out of
somebody's download history or this machine's own paths. They work; they
cannot be published as they are. Moving one across means reading it through
for personal data first.

Probes worth keeping there rather than moving: `yt_door_probe.py`,
`yt_fetch_probe.py` and `yt_fetch_probe_big.py` measure what YouTube is
currently handing out and how fast, which is how the numbers in `doors.py` and
`_door_pull` were arrived at. They answer a question rather than assert
anything, so they are not tests.

## Writing another one

Follow the shape of the others: a `check(name, condition, detail)` that counts,
sections printed as headings, and a tally at the end. Say what a failure means
in the name — `"the bytes went through the proxy too"` is a line somebody can
act on, `"test_proxy_2"` is not.

Anything that downloads writes into a temporary folder and deletes it
afterwards. Nothing here may write to the real download folder, the history
file, or settings — `test_youtube_door_engine.py` shows the pattern for
stubbing `add_history` out.
