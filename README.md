# signal-ai-bot

A Signal bot that hunts local marketplace listings (Craigslist; Facebook
Marketplace later) for furniture matching a target aesthetic, and chats with
you about it in a dedicated Signal group. Design + roadmap: [PLAN.md](PLAN.md).
Session handoff state: [WORKLOG.md](WORKLOG.md).

## Prerequisites (home box)

Docker, [Bazelisk](https://github.com/bazelbuild/bazelisk), and
[Determinate Nix](https://install.determinate.systems). Everything else is
hermetic via Bazel.

## Setup

```sh
bazel test //...                          # should be green out of the box
docker compose up -d                      # signal-cli-rest-api
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
