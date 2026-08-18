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
