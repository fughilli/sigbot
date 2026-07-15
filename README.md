# signal-ai-bot

A Signal bot that hunts local marketplace listings (Craigslist; Facebook
Marketplace later) for furniture matching a target aesthetic, and chats with
you about it in a dedicated Signal group. Design + roadmap: [PLAN.md](PLAN.md).
Session handoff state: [WORKLOG.md](WORKLOG.md).

## Prerequisites

[Bazelisk](https://github.com/bazelbuild/bazelisk) and
[Determinate Nix](https://install.determinate.systems); everything else is
hermetic via Bazel. The signal-cli-rest-api service runs either way:

- **With docker** (home box): `docker compose up -d`.
- **Without docker** (e.g. a dev container that can't nest containers):
  `scripts/install_native_services.sh` once — installs JRE 25, signal-cli,
  an arch-correct libsignal, and a source-built signal-cli-rest-api into the
  gitignored `.deps/` — then `scripts/signal_api.py start`.

`scripts/setup_signal.py` auto-detects which runtime is present.

## Setup

```sh
bazel test //...                          # should be green out of the box
docker compose up -d                      #   OR: scripts/install_native_services.sh
                                          #       && scripts/signal_api.py start --mode normal
bazel run //scripts:setup_signal          # guided bot-account registration (§4.7)
cp config.example.yaml config.yaml        # fill in the two numbers
export ANTHROPIC_API_KEY=...
bazel run //finder:finder_bot
```

On first start the bot creates the "🛋 Furniture Finder" Signal group with you
in it and walks you through your first search. Everything after that —
location, radius, price caps, cadence, reference photos — is configured by
talking to it.

## Development

- `bazel test //...` — full suite.
- Python deps: edit `requirements.in`, then
  `bazel run //:generate_requirements_lock`, commit both.
- Long-running deploy: `deploy/finder-bot.service`.
