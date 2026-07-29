# signal-ai-bot (sigbot)

A generic multi-persona Signal bot platform. One Signal account (configurable
display name) joins any number of group chats; each registered group gets its
own *service* — a persona (member label + system prompt) and its own API keys.
An HTTP server exposes a per-service API (bearer-key auth, wrapped by the
installable [`sigbot-client`](client/) wheel) plus a login-protected admin
dashboard where services are registered and keys are minted.

Bots built on the service API live in their own repos — e.g.
[marketplace-finder-bot](https://github.com/fughilli/marketplace-finder-bot),
which drives its group through a service registered with reply policy "never".
Session handoff state: [WORKLOG.md](WORKLOG.md).

## Prerequisites

[Bazelisk](https://github.com/bazelbuild/bazelisk) and
[Determinate Nix](https://install.determinate.systems); everything else is
hermetic via Bazel. The signal-cli-rest-api service runs either way:

- **With docker** (home box): `docker compose up -d`.
- **Without docker** (e.g. a dev container that can't nest containers):
  `nix build .#signal-services -o .nix-services` — signal-cli from nixpkgs
  plus signal-cli-rest-api built from source, both pinned by `flake.lock` —
  then `scripts/signal_api.py start --mode json-rpc`.

`scripts/setup_signal.py` auto-detects which runtime is present.

## Running sigbot

```sh
bazel test //...                          # green out of the box
docker compose up -d                      #   OR: nix build .#signal-services -o .nix-services
                                          #       && scripts/signal_api.py start --mode json-rpc
bazel run //scripts:setup_signal          # bot-account registration, first time only
cp sigbot.example.yaml sigbot.yaml        # fill in the bot number
export ANTHROPIC_API_KEY=...
export SIGBOT_ADMIN_PASSWORD=...          # else one is generated + printed once
bazel run //sigbot:sigbot_server -- --workdir "$PWD"
```

`--workdir` anchors everything that must survive restarts (sigbot.yaml,
`data/` incl. the SQLite DB and Signal account data); point it at a
bind-mounted/persistent path.

The server binds one port (default 8100) carrying both surfaces:

- **Dashboard** (`/`, login required): lists the group chats the bot account
  has been added to; for each you register a service — persona label, system
  prompt, reply policy (every message / mention-only / never), optional model
  override — and mint/revoke API keys (shown once, stored hashed).
- **Service API** (`/api/v1/*`, `Authorization: Bearer sb_...`): each key is
  scoped to its service's group — read the message log
  (`GET /api/v1/messages`, incremental via `after_id`), post into the group
  as the bot (`POST /api/v1/messages`, optional `attachments_b64`), download
  incoming attachments (`GET /api/v1/attachments/{id}`), inspect the persona
  (`GET /api/v1/service`).

Personas answer in their group using the service's system prompt, with the
group's message history (including messages injected through the API) as
context. Services with reply policy "never" are transport-only: an external
bot process drives the group through the API. Admins and keys can also be
managed headlessly: `bazel run //sigbot:admin -- add-admin|set-password|
list-services|mint-key|revoke-key` (run with the workdir as cwd).

## The client API

External bots consume the service API two ways:

- **pip**: `scripts/build_client_wheel.sh` builds the zero-dependency
  `dist/sigbot_client-*.whl`; see [client/README.md](client/README.md).
- **bazel module**: depend on this repo and use
  `@signal_ai_bot//client:sigbot_client_lib`:

  ```starlark
  bazel_dep(name = "signal_ai_bot", version = "0.0.0")
  git_override(
      module_name = "signal_ai_bot",
      remote = "git@github.com:fughilli/signal-ai-bot.git",
      commit = "...",
  )
  ```

## Development

- `bazel test //...` — full suite.
- Python deps: edit `requirements.in`, then
  `bazel run //:generate_requirements_lock`, commit both.
