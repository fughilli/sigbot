# WORKLOG

_Last updated: 2026-07-29 by an agent session. Read together with `git log`._

## Goal
sigbot: generic multi-persona Signal bot platform (one account, per-group
persona services, key-scoped service API + admin dashboard). The furniture
finder that used to live here was split into its own repo on 2026-07-29:
github.com/fughilli/marketplace-finder-bot (checked out, gitignored, at
./marketplace-finder-bot in this container). It consumes
@sigbot//client:sigbot_client_lib via bazel git_override.

## State of play
- **Everything runs inside the dev container, Nix-managed** (no docker):
  `nix build .#signal-services -o .nix-services` then
  `scripts/signal_api.py start --mode json-rpc` (see git history for the
  gory details: swag pin, JRE 25, seccomp blocks nested containers).
- **LIVE right now in this container**: signal services (bot number in
  sigbot.yaml, profile "Botsy"), sigbot_server
  (bazel-bin/sigbot/sigbot_server --workdir /workspace, log
  data/sigbot-server.log, port 8100 = named service `sigbot`), and the
  finder bot from ./marketplace-finder-bot polling with its minted key.
  Dashboard admin password: rotated 2026-07-29, lives OUTSIDE the repo at
  credentials/sigbot-admin-password.txt (gitignored dir; never commit it).
- The 🛋 Furniture Finder group is registered as service 'furniture-finder'
  (respond_to=none, prefix off) in data/sigbot.db; the finder's key is in
  marketplace-finder-bot/config.yaml. End-to-end user-message round trip
  NOT yet verified — user should message the group.
- Architecture notes: services/keys/messages/admins/sessions in
  sigbot/store.py (SQLite, data/sigbot.db); listener routes envelopes by
  group id; personas reply via anthropic with the message log as context;
  API keys sha256-hashed, plaintext shown once; dashboard cookies are
  SameSite=Lax sessions. Client wheel (client/, stdlib-only) 0.2.0.
- Container plumbing: overlay.json names `sigbot`:8100 and
  `finder-dashboard`:8099 (the finder still runs in this container from its
  nested checkout). apt-install + sudo overlay entries added 2026-07-29 —
  live at next container relaunch. openssh for git comes from nix until
  then (nix build nixpkgs#openssh).

## Split status (2026-07-29)
- This repo: finder/, finder tests, fb_login, deploy/, PLAN.md,
  config.example.yaml removed; requirements slimmed (no torch/playwright);
  flake keeps only signal-services; README/WORKLOG rewritten.
- marketplace-finder-bot: full finder implementation + its tests, PLAN.md,
  playwright flake, systemd unit. finder/notify/sigbot_api.py now wraps
  sigbot_client.ServiceClient (asyncio.to_thread) from
  @sigbot//client:sigbot_client_lib via git_override (pinned commit).
  Local dev override: bazel --override_module=sigbot=/workspace.
- PUSH STATUS: see end-of-session notes / chat — container had no git
  credentials (credentials/ only has anthropic.key; no ssh key, no token).

## Packaging / CI (2026-07-29)
- //sigbot:image — SELF-CONTAINED stack image (2026-07-29 v2): base is
  bbernhard/signal-cli-rest-api:0.100 (digest-pinned; solves JRE +
  per-arch libsignal), layered with sigbot_server+admin runfiles, a
  supervisor entrypoint (/sigbot-entrypoint.sh runs upstream /entrypoint.sh
  + sigbot_server, exits if either dies), and a /usr/bin/python3 symlink
  into the hermetic runfiles interpreter (base has no python; py_binary
  stage-1 needs one; bootstrap_impl=script was tried and REVERTED — its
  .venv symlinks break aspect tar's mtree). Mount /data (sigbot.yaml with
  api_url http://127.0.0.1:8080) and /home/.local/share/signal-cli.
  //sigbot:image_load for local docker; //sigbot:image_push →
  ghcr.io/fughilli/sigbot. Verified by layer inspection + running extracted
  binaries (docker can't run in this container).
- //client:sigbot_client_wheel — bazel-built wheel (version duplicated in
  client/BUILD.bazel + pyproject.toml — bump both).
- .github/workflows/ci.yml: test → per-arch image jobs (ubuntu-24.04 +
  ubuntu-24.04-arm, native builds, push latest-{arch} + sha-{arch} tags on
  master/tags) → manifest job stitches :latest/:sha/:vX with buildx
  imagetools; wheel job uploads artifact and attaches to the release on v*
  tags. NOTE: ubuntu-24.04-arm runners are free for PUBLIC repos only —
  private repo needs a paid arm runner or the repo flipped public. CI not
  yet exercised (pushes happen from host).
- deploy/install.sh — end-user installer: commissioning (prompts bot
  number/name/anthropic key/admin creds; writes ~/sigbot/data/sigbot.yaml +
  mode-600 .env) then docker compose up of signal-api + sigbot; prints
  account provisioning options (REST register vs qrcodelink) and dashboard
  URL. Idempotent; reads prompts from /dev/tty so `curl | bash` works.

## Next up
1. Verify the live round trip: user messages the 🛋 group; finder should
   reply through sigbot (watch marketplace-finder-bot log +
   data/sigbot-server.log).
2. Register a second group + persona in the dashboard to exercise the
   persona path (http://sigbot.<instance>.claude.localhost/).
3. Possible follow-ups: rate limits on the service API, webhook push
   (instead of after_id polling), per-service persona tools.

## Don't retry (dead ends)
- Docker/podman inside this dev container — seccomp EPERMs `unshare -U`,
  CapEff=0, no /dev/fuse, no docker.sock. Native services are the way.
- JRE 21 for signal-cli 0.14.x — it's built for class file 69; needs JRE 25.
- Naming a py_binary the same as a sibling package dir
  (ArtifactPrefixConflict) — hence //sigbot:sigbot_server, not :sigbot.
