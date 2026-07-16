# WORKLOG

_Last updated: 2026-07-15 by an agent session. Read together with `git log`._

## Goal
Signal-bot agent (per PLAN.md): scrapes Craigslist/FBM for furniture matching an
aesthetic, configured conversationally over Signal, running on the user's home
box. Milestones M0–M5 in PLAN.md §7.

## State of play
- **Everything runs inside the dev container, Nix-managed** (no docker):
  nested containers are impossible here (seccomp blocks user namespaces —
  verified), so the stack runs natively from the flake:
  `nix build .#signal-services -o .nix-services` → signal-cli 0.14.5
  (nixpkgs, binary-cached even on aarch64) + signal-cli-rest-api 0.100
  (buildGoModule in flake.nix; swag pinned to 1.16.4 — nixpkgs' 1.16.6
  can't parse upstream's annotations). flake.lock committed.
  `scripts/signal_api.py start|stop|restart --mode normal|json-rpc`
  replaces docker compose; setup_signal.py auto-detects docker vs native.
  **Live-verified:** json-rpc mode up from Nix binaries, daemon socket
  connected, /v1/about + /v1/accounts good. Services left RUNNING in
  json-rpc mode (pids in data/signal-api/).
- Nix ships in the BASE container image, but the `default` profile symlink
  dangles (per-user dirs deleted by the image) so `nix` isn't on PATH; the
  overlay now repairs the profile (takes effect next container launch).
  Until then: /nix/store/*-determinate-nix-*/bin/nix.
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
1. Register the bot account — can now happen IN THIS CONTAINER:
   `bazel run //scripts:setup_signal` (needs the user for GV number +
   captcha + SMS code), then config.yaml + ANTHROPIC_API_KEY, then
   `bazelisk run //finder:finder_bot` → verify M1 exit criterion (create/edit
   a query entirely from Signal).
2. M2: Craigslist fetcher (httpx + selectolax — add selectolax to
   requirements.in and regen lock), hard filters, notify path in
   pipeline.run_pass, heartbeat setting. PLAN.md §4.3/§7.
3. M3: CLIP scoring (open_clip + torch CPU pin), Claude judge.

## Open questions / blockers
- End-to-end Signal verification blocked on home-box access (user runs setup).
- Group send-id vs internal-id handling in notify/groups.py is per docs;
  verify against a real /v1/groups response.

## Don't retry (dead ends)
- Naming the py_binary `//finder:bot` — output path collides with the
  finder/bot/ package dir (ArtifactPrefixConflict). It's `//finder:finder_bot`.
- Docker/podman inside this dev container — seccomp EPERMs `unshare -U`,
  CapEff=0, no /dev/fuse, no docker.sock. Native services are the way.
- JRE 21 for signal-cli 0.14.x — it's built for class file 69; needs JRE 25.
