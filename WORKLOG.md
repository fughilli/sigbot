# WORKLOG

_Last updated: 2026-07-29 by an agent session. Read together with `git log`._

## Goal
Two bots now: (1) **sigbot** — generic multi-persona Signal bot platform
(new 2026-07-29, user-requested pivot); (2) the original furniture finder
(PLAN.md), unchanged and still fully tested.

## sigbot (generic persona bot platform)
- `sigbot/` package, self-contained (no finder imports; signal_client.py is a
  copy of finder/notify/signal.py + set_profile_name). One Signal account,
  display name from `bot_name` in sigbot.yaml; per-group **services** =
  persona (label + system prompt) + reply policy + API keys, all in
  data/sigbot.db (`sigbot/store.py`).
- HTTP server on :8100 (`sigbot/api.py`): `/api/v1/*` bearer-key service API
  (send to group / read message log with after_id cursor / service info) and
  a session-cookie admin dashboard (`/login`, static/dashboard.html) that
  registers services against the bot's live group list and mints/revokes keys
  (sha256-hashed, plaintext shown once). Admin bootstrap on first run:
  $SIGBOT_ADMIN_PASSWORD or generated+printed; `//sigbot:admin` CLI for
  add-admin/set-password/mint-key etc. (run with workdir as cwd — db_path is
  cwd-relative; `bazel run` chdirs to BUILD_WORKING_DIRECTORY=invocation dir).
- Personas reply via anthropic using the message log as context
  (`sigbot/agent.py`: build_llm_messages merges consecutive roles, senders
  prefixed "Name: text"; API-injected messages count as assistant turns).
  respond_to: all|mention (mention = signal mention of bot number, or
  label/bot_name substring). Outgoing prefixed "[label] " unless disabled.
- Client wheel: `client/` (stdlib-only `sigbot_client.ServiceClient`);
  `scripts/build_client_wheel.sh` → dist/ (falls back to bazel hermetic
  python + vendored pip — system python3 in this container has no pip).
- Port forwarding: overlay.json names service `sigbot` → 8100 (works without
  container relaunch); finder dashboard also named `finder-dashboard` → 8099.
- Tests: tests/sigbot_{store,listener,agent,api,client}_test.py, all green
  (`bazel test //...` 14/14). Live smoke-tested end-to-end in /tmp/sigbot-smoke:
  server boot, login, admin API, CLI-minted key used through the installed
  wheel. NOT yet run against live Signal.
- **Not started live**: sigbot.yaml (gitignored) is prefilled with the
  finder's account +15555550100 — finder_bot and sigbot_server must not run
  at once on one account (both consume the same receive websocket). Register
  a second account via scripts/setup_signal.py if both should be live.

## State of play (furniture finder)
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
- **M0, M1 done and user-verified**: bot account +15555550100 registered
  (backup in data/backups/), group live, user configured a real query
  (mcm-dining-chairs, 94103, 20mi) entirely from Signal chat. User is
  identified by ACI uuid (see config.yaml, gitignored; API key at
  credentials/anthropic.key, gitignored).
- **M2 + dashboard done** (see `c691c45`, `66efbe5`): Craigslist fetcher
  (static-markup parser, live fixtures), judgement-trail pipeline, web
  dashboard on 127.0.0.1:8090 (browser access from host needs the next
  container relaunch to pick up overlay.json's port 8090). No real pass
  has run yet.
- **Bot is RUNNING in tmux window `finder-bot`** (bazelisk run
  //finder:finder_bot with ANTHROPIC_API_KEY from credentials/). Signal
  services run natively via scripts/signal_api.py (json-rpc mode).
- `flake.lock` is NOT generated yet — no nix in this dev container until the
  overlay (committed in M0) takes effect on next container launch. Run
  `nix flake lock` then commit it.
- Docker is unavailable in the dev container; signal-cli-rest-api and
  scripts/setup_signal.py are untested against a live API. Endpoint shapes
  (group create response, PIN endpoint) follow bbernhard/signal-cli-rest-api
  docs but expect small fixups on first real run.
- pipeline.run_pass is an M1 stub — reports "no fetchers enabled yet".

## Recent
- Re-evaluation + coverage rework: listings re-judged when a query's
  criteria change (criteria_hash), cached-image disk cache, progressive
  judge coverage (deferred below-top-K carried forward), 40 new/pass,
  reevaluate_query tool. See the commit for details.
- Craigslist 180-char query cap fix (was 404ing the 10-keyword query).

## Next up
0. sigbot live run: start signal services (scripts/signal_api.py start
   --mode json-rpc), decide which account sigbot uses (see above), run
   `bazel run //sigbot:sigbot_server -- --workdir "$PWD"`, add the account to
   a test group, register a service in the dashboard
   (http://sigbot.<instance>.claude.localhost/), verify a persona reply and
   an API send. Possible follow-ups: rate limits on the service API, webhook
   push (instead of after_id polling), per-service tools.
1. M3 (CLIP+judge) and M4 (Facebook) both shipped but NOT yet exercised
   on a real pass. For M3: send the bot reference photos -> stored in
   references/mcm-dining-chairs/, then "run now" (first scored pass
   downloads ~600MB CLIP weights to cache/models/). For M4: on the box,
   `nix build .#playwright-browsers -o .playwright-browsers`, export the
   two PLAYWRIGHT_* env vars, `bazel run //scripts:fb_login`, then enable
   sources.facebook. FBM parser is fixture-tested only — expect the
   embedded-JSON shape to need adjustment against a real logged-in page
   (parse_search_payload / _iter_listing_nodes in fetchers/facebook.py).
2. Heartbeat setting ("checked N, 0 matches" daily) — deferred from M2.
3. Milestones M0-M4 all code-complete. Remaining polish (PLAN M5): repost
   tuning, signal-volume backup automation, more agent tools (e.g.
   "retry facebook" to reset the circuit breaker manually).

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
