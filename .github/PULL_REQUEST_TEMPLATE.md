## What this changes

<!-- One or two sentences. Why, not just what. -->

## How I tested it

<!-- Be specific. "It builds" is not testing. -->

- [ ] `python3 -m unittest discover -s tests -p '*_tests.py' -t .` passes
- [ ] Tested against a real Amazon account in **preview** mode
- [ ] Tested against a real Amazon account with `--apply`
- [ ] Mac app builds (`xcodebuild ... build`)

## What I did NOT test

<!-- Markets, product types or paths you could not reach. This is useful, not embarrassing. -->

## Safety checklist

Tick only what applies. Delete the section if this PR touches no write path.

- [ ] Calls `killswitch.check()` before touching Amazon
- [ ] Passes the relevant `db.snapshot_gate(...)`
- [ ] Goes through `ads_client` so bid and budget ceilings clamp it
- [ ] Logs to `writes_log` with the previous value, so undo works
- [ ] Previews by default; `--apply` is opt-in
- [ ] Resolves dates from the table it reads — never from another perf table
- [ ] Fails closed when economics or data are unavailable

## Anything else

<!-- Trade-offs, open questions, follow-up work. -->
