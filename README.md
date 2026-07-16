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

## Development

- `bazel test //...` — full suite.
- Python deps: edit `requirements.in`, then
  `bazel run //:generate_requirements_lock`, commit both.
- Long-running deploy: `deploy/finder-bot.service`.
