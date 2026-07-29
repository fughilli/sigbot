# sigbot

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

## Install (prebuilt images — no bazel needed)

CI publishes a multi-arch (amd64/arm64) image to `ghcr.io/fughilli/sigbot`
that bundles the whole stack: sigbot **plus** signal-cli-rest-api/signal-cli
(built on the upstream bbernhard image), supervised by one entrypoint.
On any box with docker:

```sh
curl -fsSL https://raw.githubusercontent.com/fughilli/sigbot/master/deploy/install.sh | bash
```

(While this repo is private: `docker login ghcr.io` first, and fetch the
script with an authenticated clone instead of raw curl.)

The script pulls the image, runs **commissioning** — prompts for the bot
number/name, Anthropic API key, and dashboard admin credentials, then writes
`~/.config/sigbot/sigbot.yaml` plus a mode-600 `.env` with the secrets — and installs
the **`sigbot-d`** command. Nothing starts until you run:

```sh
sigbot-d                        # start detached (default dir ~/.config/sigbot)
sigbot-d status                 # …plus stop / logs
sigbot-d /path/to/other-dir     # or point any command at another instance
```

`sigbot-d` runs the container in the background with docker's restart policy;
the data dir holds everything (config, secrets, SQLite DB, signal account
data), so several instances with different data dirs can coexist
(`SIGBOT_PORT`/`SIGNAL_PORT` env vars pick the ports). The installer prints
the account-provisioning steps (register a number, or QR-link an existing
account) and the dashboard URL. Re-running it keeps existing config.

## Building the image yourself

With bazelisk set up, the same image CI publishes is a target:

```sh
bazel run //sigbot:image_load        # loads sigbot:latest into docker
docker run --rm --env-file .env -p 8100:8100 -p 127.0.0.1:8080:8080 \
  -v "$PWD/data:/data" -v "$PWD/data/signal-cli:/home/.local/share/signal-cli" \
  sigbot:latest
```

(`//sigbot:image` is the raw OCI layout. The entrypoint supervises the
bundled signal-cli-rest-api and `sigbot_server --workdir /data`; mount your
data/ dir — with sigbot.yaml inside, `signal.api_url: http://127.0.0.1:8080`
— at `/data`, and the signal account data as shown. The admin CLI ships at
`/sigbot/admin`.) Or skip containers entirely and
`bazel run //sigbot:sigbot_server` as below.

## Prerequisites (from-source path)

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

- **pip**: grab `sigbot_client-*.whl` from the GitHub releases page (CI
  attaches it on version tags), or build it yourself:
  `bazel build //client:sigbot_client_wheel` (or, without bazel,
  `scripts/build_client_wheel.sh`). See [client/README.md](client/README.md).
- **bazel module**: depend on this repo and use
  `@sigbot//client:sigbot_client_lib`:

  ```starlark
  bazel_dep(name = "sigbot", version = "0.0.0")
  git_override(
      module_name = "sigbot",
      remote = "git@github.com:fughilli/sigbot.git",
      commit = "...",
  )
  ```

## Development

- `bazel test //...` — full suite.
- Python deps: edit `requirements.in`, then
  `bazel run //:generate_requirements_lock`, commit both.
