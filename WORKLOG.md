# WORKLOG

_Last updated: 2026-07-15 by an agent session. Read together with `git log`._

## Goal
Signal-bot agent (per PLAN.md): scrapes Craigslist/FBM for furniture matching an
aesthetic, configured conversationally over Signal, running on the user's home
box. Milestones M0–M5 in PLAN.md §7.

## State of play
- **M0 done** (see `bb2d5ed`): Bazel+Nix scaffold, `bazel test //...` green.
- **M1 code-complete** (see subsequent commits): store, Signal client/group/
  listener, Claude agent loop + 12 validated tools, live-reschedulable
  APScheduler, setup_signal script, systemd unit, README. 5/5 test targets
  green. **Not yet verified end-to-end** — that needs the home box (docker +
  a GV number). M1 exit criterion (create/edit a query entirely from Signal)
  is still open until then.
- `flake.lock` is NOT generated yet — no nix in this dev container until the
  overlay (committed in M0) takes effect on next container launch. Run
  `nix flake lock` then commit it.
- Docker is unavailable in the dev container; signal-cli-rest-api and
  scripts/setup_signal.py are untested against a live API. Endpoint shapes
  (group create response, PIN endpoint) follow bbernhard/signal-cli-rest-api
  docs but expect small fixups on first real run.
- pipeline.run_pass is an M1 stub — reports "no fetchers enabled yet".

## Next up
1. On the home box: docker compose up, `bazel run //scripts:setup_signal`
   (GV number + captcha), config.yaml, run the bot, verify M1 exit criterion.
   Fix any signal-cli-rest-api endpoint mismatches found.
2. Generate + commit flake.lock (first machine with nix).
3. M2: Craigslist fetcher (httpx + selectolax — add selectolax to
   requirements.in and regen lock), hard filters, notify path in
   pipeline.run_pass, heartbeat setting. PLAN.md §4.3/§7.
4. M3: CLIP scoring (open_clip + torch CPU pin), Claude judge.

## Open questions / blockers
- End-to-end Signal verification blocked on home-box access (user runs setup).
- Group send-id vs internal-id handling in notify/groups.py is per docs;
  verify against a real /v1/groups response.

## Don't retry (dead ends)
- Naming the py_binary `//finder:bot` — output path collides with the
  finder/bot/ package dir (ArtifactPrefixConflict). It's `//finder:finder_bot`.
