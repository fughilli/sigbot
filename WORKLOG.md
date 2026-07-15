# WORKLOG

_Last updated: 2026-07-15 by an agent session. Read together with `git log`._

## Goal
Signal-bot agent (per PLAN.md): scrapes Craigslist/FBM for furniture matching an
aesthetic, configured conversationally over Signal, running on the user's home
box. Milestones M0–M5 in PLAN.md §7.

## State of play
- M0 scaffold in progress this session; `bazel test //...` green (2 tests).
- Nix is NOT installed in this dev container yet — the overlay Dockerfile adds
  it on next container launch. Consequence: `flake.lock` is not yet generated.
  Run `nix flake lock` once nix is available (next container restart, or the box)
  and commit it.
- Docker is not available in the dev container; signal-cli-rest-api can only be
  exercised for real on the home box.

## Next up
1. M1 store + config layer (in progress)
2. M1 Signal client / groups / listener
3. M1 agent loop + tools; setup_signal script

## Open questions / blockers
- End-to-end Signal verification needs the home box (GV number, captcha step).

## Don't retry (dead ends)
- (none yet)
