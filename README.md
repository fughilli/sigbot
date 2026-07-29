# signal-ai-bot

Two Signal bots share this repo and its signal-cli-rest-api infrastructure:

- **sigbot** (`sigbot/`) — a generic multi-persona bot platform. One Signal
  account (configurable display name) joins any number of group chats; each
  registered group gets its own *service*: a persona (member label + system
  prompt) and its own API keys. An HTTP server exposes a per-service API
  (bearer-key auth, wrapped by the installable [`sigbot-client`](client/)
  wheel) plus a login-protected admin dashboard where services are registered
  and keys are minted. See [Generic persona bot](#generic-persona-bot-sigbot).
- **finder** (`finder/`) — the original furniture finder: hunts local
  marketplace listings (Craigslist; Facebook Marketplace) matching a target
  aesthetic and chats about it in a dedicated group. Design + roadmap:
  [PLAN.md](PLAN.md).

Session handoff state: [WORKLOG.md](WORKLOG.md).

## Generic persona bot (sigbot)

```sh
cp sigbot.example.yaml sigbot.yaml            # fill in the bot number
export ANTHROPIC_API_KEY=...
export SIGBOT_ADMIN_PASSWORD=...              # else one is generated + printed once
bazel run //sigbot:sigbot_server -- --workdir "$PWD"
```

The server binds one port (default 8100) carrying both surfaces:

- **Dashboard** (`/`, login required): lists the group chats the bot account
  has been added to; for each you register a service — persona label, system
  prompt, reply policy (every message vs. mention-only), optional model
  override — and mint/revoke API keys (shown once, stored hashed).
- **Service API** (`/api/v1/*`, `Authorization: Bearer sb_...`): each key is
  scoped to its service's group — read the message log
  (`GET /api/v1/messages`, incremental via `after_id`), post into the group as
  the bot (`POST /api/v1/messages`), inspect the persona
  (`GET /api/v1/service`).

Personas answer in their group using the service's system prompt, with the
group's message history (including messages injected through the API) as
context. Admins and keys can also be managed headlessly:
`bazel run //sigbot:admin -- add-admin|set-password|list-services|mint-key|revoke-key`
(run it with the workdir as cwd so it finds `sigbot.yaml` and the DB).

The API client ships as a zero-dependency wheel — build it with
`scripts/build_client_wheel.sh`, install `dist/sigbot_client-*.whl` anywhere,
and use `sigbot_client.ServiceClient` (see [client/README.md](client/README.md)).

Note: the two bots can share signal-cli-rest-api but not one account's receive
stream — run either `finder_bot` or `sigbot_server` per account, not both.

## Furniture finder

## Prerequisites

[Bazelisk](https://github.com/bazelbuild/bazelisk) and
[Determinate Nix](https://install.determinate.systems); everything else is
hermetic via Bazel. The signal-cli-rest-api service runs either way:

- **With docker** (home box): `docker compose up -d`.
- **Without docker** (e.g. a dev container that can't nest containers):
  `nix build .#signal-services -o .nix-services` — signal-cli from nixpkgs
  plus signal-cli-rest-api built from source, both pinned by `flake.lock` —
  then `scripts/signal_api.py start`.

`scripts/setup_signal.py` auto-detects which runtime is present.

## Setup

```sh
bazel test //...                          # should be green out of the box
docker compose up -d                      #   OR: nix build .#signal-services -o .nix-services
                                          #       && scripts/signal_api.py start --mode normal
bazel run //scripts:setup_signal          # guided bot-account registration (§4.7)
cp config.example.yaml config.yaml        # fill in the two numbers
export ANTHROPIC_API_KEY=...
bazel run //finder:finder_bot -- --workdir "$PWD"
```

`--workdir` anchors everything that must survive container restarts
(config.yaml, `data/` incl. the SQLite DB and Signal account data,
`references/`, `cache/`); point it at a bind-mounted/persistent path.

On first start the bot creates the "🛋 Furniture Finder" Signal group with you
in it and walks you through your first search. Everything after that —
location, radius, price caps, cadence, reference photos — is configured by
talking to it.

## Facebook Marketplace (optional)

Off by default. To enable:

```sh
nix build .#playwright-browsers -o .playwright-browsers
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
bazel run //scripts:fb_login          # log in by hand (headed browser)
```

Then tell the bot to enable Facebook (or set `sources.facebook.enabled: true`).
Meta's anti-bot is aggressive — a login wall trips a 24h circuit breaker and
pings the group to re-run `fb_login`. Best run from the home box's residential
IP.

## Development

- `bazel test //...` — full suite.
- Python deps: edit `requirements.in`, then
  `bazel run //:generate_requirements_lock`, commit both.
- Long-running deploy: `deploy/finder-bot.service`.
