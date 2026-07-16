# Furniture Finder Agent — Implementation Plan

A long-running agent on a home server that periodically scrapes Craigslist and Facebook Marketplace for furniture near you, scores each listing's photos and prose against a target aesthetic (e.g. a Pinterest board), and surfaces new matches in a dedicated Signal group. The same Signal conversation is the control surface: you configure region, radius, queries, price caps, and cadence by talking to the bot, and discuss results ("more photos of #2?", "fewer tufted things").

## 1. Decisions locked in

- **Host:** a box at your house (residential IP — the best case for Facebook Marketplace).
- **Signal identity:** a dedicated bot account registered on a Google Voice number — the agent is never logged into your primary account.
- **Conversation model:** one Signal **group per function**, created by the bot, containing just you + the bot. (A 1:1 chat between two numbers is a single thread, so groups are how "separate conversations per function" is realized.) This application gets one group: results + discussion + configuration.
- **FBM access:** your own Facebook account via a persistent Playwright profile.
- **Configuration:** region/radius/queries/cadence are set conversationally; no config-file editing in normal operation.

## 2. Feasibility notes & risks

**Craigslist — easy-ish.** No public API and RSS is mostly dead, but search-result pages are plain HTML and fine to fetch at personal-use volume (a few requests per run). CL does CAPTCHA/IP-block aggressive scrapers, so: low frequency, jittered timing, cache everything. This is the reliable backbone.

**Facebook Marketplace — the hard part.** No public API; Meta's anti-bot (device fingerprinting, login walls, WAF) is among the most aggressive anywhere. Plan: Playwright with a persistent logged-in profile, human-like pacing, a few runs/day, first ~2 pages per query. Residential IP helps materially. Fallbacks if it fights back: replaying the marketplace GraphQL call with real session cookies, or a paid scraper API (Apify etc.) behind the same fetcher interface. Small but real risk of account flagging at any volume — the circuit breaker (below) keeps the bot polite. ToS on both sites prohibits automated access; personal-use volume is a gray area you've accepted.

**Signal bot account registration.** `bbernhard/signal-cli-rest-api` (Docker) supports registering a number natively (not just linking). The full account-creation workflow is scripted in §4.7; only the captcha and the SMS code are manual. Two GV caveats: keep the GV number minimally active so Google doesn't reclaim it, and back up the container's signal data volume — losing it means re-registering. Receiving messages uses the container's `json-rpc` mode (websocket), which the bot daemon consumes.

**Pinterest ingestion** — official API needs app approval; simplest is `gallery-dl` (supports Pinterest boards) run occasionally to sync board images into `references/`. You can also just send reference photos to the bot in Signal — attachments from you get saved into the active query's reference set. That may end up being the primary path.

## 3. Architecture

One docker-compose stack, two services, always on:

```
┌──────────────────────────── home box ────────────────────────────────┐
│                                                                      │
│  signal-cli-rest-api (json-rpc mode)          finder-bot (Python)    │
│  ┌────────────────────┐   websocket   ┌────────────────────────────┐ │
│  │ bot Signal account │◀─────────────▶│ listener: msgs from YOU    │ │
│  │ (GV number)        │               │   └▶ Claude agent loop     │ │
│  └────────┬───────────┘               │      + tools (see 4.2)     │ │
│           │ Signal network            │                            │ │
│           ▼                           │ scheduler (APScheduler)    │ │
│   "🛋 Furniture Finder" group          │   └▶ scrape pass:          │ │
│    (you + bot)                        │      fetchers → normalize  │ │
│                                       │      → dedup → match       │ │
│                                       │      → post to group       │ │
│                                       │                            │ │
│                                       │ SQLite: listings, queries, │ │
│                                       │ settings, chat history     │ │
│                                       └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

The scheduler lives inside the bot daemon (not host cron) so "check every 2 hours instead" takes effect immediately via conversation. Runs are jittered ±20 min and mutex-locked. A fetcher raising an exception logs and skips; other sources still complete.

### Scrape pass pipeline

fetch → normalize → drop already-seen (SQLite) → stage-0 hard filters (price/keywords) → stage-1 CLIP score vs. reference embeddings → stage-2 Claude vision judge (top-K) → post matches to group → mark seen.

### Listing schema (normalized)

```
id (source:native_id), source, title, description, price, currency,
url, image_urls[], location_text, lat/lon (if available), posted_at,
first_seen_at, clip_score, judge_verdict, judge_reason, notified_at,
short_ref (e.g. "#14" — how you refer to it in chat)
```

## 4. Components

### 4.1 Configuration model

- **`config.yaml` (static infra, edited once):** signal API URL, bot number, your number (allowlist), Anthropic API key env ref, source toggles, FBM profile dir.
- **SQLite `queries` + `settings` tables (dynamic, bot-managed):** location (postal, CL site), radius, cadence, per-query keywords/price cap/aesthetic description/CLIP threshold/judge top-K, paused flags. Everything the bot can change lives here, with validation in the tool layer (e.g. radius 1–100 mi, cadence ≥ 30 min).

First-run onboarding: when the bot starts with no queries defined, it messages the group: "I'm set up. Where should I search, how far, and what are we hunting for?" — your answers seed the first query conversationally.

### 4.2 Conversational agent — `bot/agent.py`

The listener consumes the websocket; **only messages from your number are processed** (hard allowlist — anyone else messaging the bot gets silence). Each message from you goes to a Claude agent loop (`claude-sonnet-4-6`, anthropic SDK tool-use) with a rolling per-group history (persisted in SQLite, trimmed to recent turns) and these tools:

| Tool | Purpose |
|---|---|
| `get_config` / `set_location` / `set_cadence` | region, radius, CL site, schedule |
| `list_queries` / `upsert_query` / `pause_query` | manage searches (keywords, price cap, aesthetic description, thresholds) |
| `add_reference_images` | attach incoming Signal photos to a query's reference set |
| `run_now` | trigger an immediate scrape pass |
| `get_listing` / `list_recent` | pull details/photos for "#14", recent matches, near-misses |
| `mark_feedback` | "yes more like this / no" on a listing — logged for threshold tuning |
| `status` | last run time, counts, source health, next run |

Examples this design covers: "search within 25 miles of 94103", "actually cap couches at $500", "check twice a day instead", "show me what almost matched yesterday", "here are 4 more inspo pics" (with attachments), "pause the credenza search".

Replies post back to the same group. The judge/scorer never talk to you directly; only the agent does.

### 4.3 Fetchers

- **`fetchers/craigslist.py`** — `httpx` + selectolax against `https://{site}.craigslist.org/search/{category}?query=...&search_distance=...&postal=...&max_price=...`. Parse result cards; fetch detail pages only for unseen listings (~5–20 requests/run). Images cached to `cache/images/`.
- **`fetchers/facebook.py`** — Playwright, persistent context in `.fb-profile/` (log in once interactively; session persists). Marketplace search URL with radius/price params, scroll twice, extract from embedded JSON. Detail pages only for unseen. **Circuit breaker:** on checkpoint/login-wall detection, disable the source for 24h and report it in the group ("FB wants a manual login — visit the box when convenient") instead of retrying.

Shared polite-fetch helper: 2–6 s randomized delays, retry-once, response caching in development.

### 4.4 Matching engine — `match/`

**Stage 0 — hard filters (free):** price ≤ cap, keyword sanity check, ≥1 image.

**Stage 1 — CLIP retrieval (cheap, local, CPU):** `open_clip` ViT-B/32 (SigLIP if quality disappoints). Reference-board images embed once into a board vector (plus individual vectors). Per listing: embed up to 4 photos; score = max cosine vs. board vector, lightly blended with similarity to the aesthetic text. Pass if ≥ threshold. `mark_feedback` data feeds threshold adjustment over time.

**Stage 2 — Claude vision judge (precise, metered):** top-K survivors → Claude with listing photos + prose + aesthetic description + 3–4 exemplar reference images → `{match, confidence, one_line_reason}`. The reason string goes straight into the group message. `judge_top_k` caps per-run spend.

Stage 1 exists so stage 2 stays cheap; stage 2 exists because CLIP alone will happily surface a beige IKEA Ektorp as "mid-century."

### 4.5 Notifier — `notify/signal.py`

`POST /v2/send` to the group with text + first photo attachment, one message per match:

> 🛋 **#14 · $450 — Mid-century walnut loveseat** (craigslist, 6 mi)
> Low walnut frame, tapered legs, mustard cushions — strong match
> https://sfbay.craigslist.org/...

Daily heartbeat (configurable off): "checked 214 listings, 0 matches" — so silence is distinguishable from breakage. Failed sends leave listings unmarked; they re-notify next pass.

### 4.6 Group lifecycle — `notify/groups.py`

On first start the bot creates the "🛋 Furniture Finder" group via `POST /v1/groups/{number}` with you as the only other member, and stores the group ID in settings. Future functions (this bot or siblings) each create their own group — per-function conversations as requested.

### 4.7 Signal account creation workflow — `scripts/setup_signal.py`

A guided, **idempotent** setup script: it checks the container's actual state (`GET /v1/accounts`, group list, config.yaml contents) and resumes from whatever step is incomplete, so a failed run is just re-run. Two steps are inherently manual (Signal's captcha, reading the SMS code); the script prompts for those and automates everything else.

**Phase 0 — preflight (automated).** Docker present; `signal-cli-rest-api` container up **in `normal` mode** (registration endpoints are only reliable there — `json-rpc` mode expects already-registered accounts at startup); data volume mounted at a persistent path; GV number entered and E.164-normalized.

**Phase 1 — captcha (manual, ~1 min).** Script prints the URL `https://signalcaptchas.org/registration/generate.html`, you solve it in a browser and paste back the resulting `signalcaptcha://...` value (script strips the scheme; tokens expire in a few minutes, so this happens immediately before Phase 2).

**Phase 2 — register (automated + one manual read).** `POST /v1/register/{number}` with the captcha token. The code arrives as SMS at Google Voice; you paste it in. If no SMS after ~2 min, script retries with `"use_voice": true` (GV answers and transcribes the call). Then `POST /v1/register/{number}/verify/{code}`.

**Phase 3 — account hardening (automated).** Set a registration-lock PIN (stored in config next to the API key — prevents anyone re-registering the GV number out from under the bot); set profile display name "Furniture Finder" and an avatar via `PUT /v1/profiles/{number}` so the message request you receive is recognizable.

**Phase 4 — switch to service mode (automated).** Flip the container to `MODE=json-rpc`, restart, wait for the websocket to accept connections.

**Phase 5 — first contact + group (automated + one tap on your phone).** Send a hello to your number, script waits while you **accept the message request** on your phone (a new number messaging you lands as a request), then create the "🛋 Furniture Finder" group and store its ID in settings. Test round-trip: script posts to the group and confirms delivery receipt.

**Phase 6 — backup (automated).** Tar the signal data volume to `data/backups/signal-<date>.tar.gz` and print the restore one-liner. The script also registers a monthly reminder in the bot's scheduler to re-backup and to nudge you if the GV number has been idle (Google reclaims inactive numbers).

**Failure modes:**

| Symptom | Cause | Recovery |
|---|---|---|
| 429 on register | Signal rate limit | Wait the `retry-after`, re-run script (fresh captcha) |
| "captcha required/invalid" | Token expired or reused | Re-run from Phase 1 |
| SMS never arrives | GV filtering / voice-only | Script's `use_voice` retry; check GV spam folder |
| Container has no accounts after restart | Data volume not persisted | Fix mount, restore from backup tar, else full re-register |
| GV number reclaimed | Number inactivity | New GV number, full re-register; old group is abandoned, script creates a new one |

Re-registration (volume loss) is the same flow end-to-end; you'll see a safety-number change notice in Signal, which is expected.

## 5. Tech stack & build system (Bazel + Nix)

**Runtime stack:** Python 3.12 · asyncio + websockets (Signal receive) · APScheduler · httpx + selectolax (CL) · Playwright (FBM) · open_clip_torch (CPU) · anthropic SDK (agent + judge) · sqlite3 · signal-cli-rest-api in `json-rpc` mode (Docker) · gallery-dl (optional Pinterest sync).

The project is built and managed with **Bazel (bzlmod, no WORKSPACE)** plus **Nix** for the pieces pip can't pin. The home box and the dev container need only Bazelisk, Nix, and Docker — no system Python.

### 5.1 Bazel

- **Pinned Bazel via Bazelisk:** `.bazelversion` (7.7.1) + `.bazeliskrc` with `BAZELISK_HOME=.bazelisk` so the Bazel binary cache lives in-tree. Always invoke `bazelisk`.
- **`MODULE.bazel`:** `rules_python` with a hermetic Python **3.12** toolchain (`is_default = True`); `pip.parse(hub_name = "pypi", requirements_lock = "//:requirements.lock")`; `rules_uv` for lockfile generation; `rules_nixpkgs_core` pinned but **registration-only** (see 5.2). `MODULE.bazel.lock` committed.
- **Python lockfile pair:** human-edited `requirements.in` (direct deps only) → `bazel run //:generate_requirements_lock` (rules_uv `pip_compile`) → committed `requirements.lock`. Never hand-edit the lock. BUILD files import deps as `@pypi//httpx`, `@pypi//torch`, etc.
- **Torch note:** pin the **CPU** wheel index in `requirements.in` (extra-index-url for `torch` CPU builds) — no CUDA payload; still ~200 MB, a one-time cost thanks to the repository cache.
- **`.bazelrc`:** `--incompatible_strict_action_env`; in-tree caches `--disk_cache=.bazel-disk-cache` and `--repository_cache=.bazel-repo-cache` (survive dev-container restarts; harmless on the box) with `--experimental_disk_cache_gc_max_size=10G`; `test --test_output=errors`. `.bazelignore` excludes `.venv`, `cache/`, `references/`, `data/`.
- **Targets:** `py_binary //finder:bot` (the daemon), `py_binary //scripts:setup_signal`, `py_test` suites for parsers (fixture HTML), tool-layer validation, and store migrations. CI-style check: lockfile-freshness via the rules_uv check target + `bazel test //...`.

### 5.2 Nix

Nix covers what the pip lockfile can't — binaries with system-level dependencies — while staying **out of the Bazel critical path**: `bazel build //...` stays green on machines without `nix`; nothing in the build graph evaluates Nix.

- **`flake.nix` + committed `flake.lock`** providing a dev shell and a runtime env package: `bazelisk`, `gallery-dl`, and — the key one — **`playwright-driver.browsers`**. Playwright's own `playwright install` downloads unpinned browsers at runtime and needs a pile of system libs; the nixpkgs derivation pins both. The systemd unit and dev shell export `PLAYWRIGHT_BROWSERS_PATH=${playwright-driver.browsers}` and `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true`. **Constraint:** the `playwright` version in `requirements.in` must match the nixpkgs driver version — bump them together, noted in the decision log.
- **`rules_nixpkgs_core` stays registration-only** — no `nix_repo`/`nixpkgs_package` in `MODULE.bazel` unless a Nix-built library is someday needed *inside* the build graph.
- **On the home box (and dev container overlay):** install Nix once via the Determinate installer (`--init none`, flakes enabled). Box setup is then: Determinate Nix + Bazelisk + Docker; everything else is pulled hermetically.

### 5.3 Deployment — box and dev container

`docker-compose.yml` owns `signal-cli-rest-api` where docker exists (the box). The bot runs as a systemd unit executing `bazelisk run //finder:finder_bot` from the repo checkout, with the flake's Playwright env vars set; `Restart=on-failure`. Updates are `git pull && systemctl restart finder-bot` — Bazel rebuilds only what changed.

**Dockerless fallback (dev container):** the dev container cannot nest containers (seccomp blocks unprivileged user namespaces; no docker socket, no capabilities), so the same stack runs natively there, fully Nix-managed: `nix build .#signal-services -o .nix-services` provides `signal-cli` (from nixpkgs — JRE and arch-correct libsignal included) and `signal-cli-rest-api` (a `buildGoModule` derivation in our flake, swag docs generated at build time), both pinned by `flake.lock`. `scripts/signal_api.py` replicates the container entrypoint (normal mode, or json-rpc mode = `jsonrpc2.yml` port map + `signal-cli daemon --tcp`). `setup_signal.py` auto-detects docker vs native. The box can use either path.

## 6. Repo layout

```
furniture-finder/
├── MODULE.bazel              # rules_python 3.12 + pip hub + rules_uv + rules_nixpkgs_core
├── MODULE.bazel.lock         # committed
├── BUILD.bazel               # root: generate_requirements_lock target
├── .bazelversion / .bazeliskrc / .bazelrc / .bazelignore
├── requirements.in           # direct Python deps (human-edited)
├── requirements.lock         # resolved (generated; committed)
├── flake.nix / flake.lock    # dev shell + playwright browsers + gallery-dl
├── config.yaml               # static infra only
├── docker-compose.yml        # signal-cli-rest-api
├── finder/
│   ├── BUILD.bazel           # py_binary :bot + py_library targets
│   ├── main.py               # starts listener + scheduler
│   ├── bot/{listener,agent,tools}.py
│   ├── store.py              # listings, queries, settings, chat history
│   ├── fetchers/{base,craigslist,facebook}.py
│   ├── match/{clip_scorer,judge}.py
│   └── notify/{signal,groups}.py
├── scripts/
│   ├── BUILD.bazel           # py_binary :setup_signal
│   └── setup_signal.py       # guided, resumable account-creation workflow (§4.7)
├── deploy/finder-bot.service # systemd unit (bazelisk run + playwright env)
├── references/<query>/       # reference images (Signal attachments / gallery-dl)
├── cache/images/
├── data/                     # listings.db + signal-cli volume backup target
└── tests/                    # py_test: fixture HTML parsers; tool-layer validation
```

## 7. Milestones

**M0 — Build scaffold.** Bazel skeleton (`MODULE.bazel`, `.bazelrc`, Bazelisk pin), `requirements.in` → generated `requirements.lock`, `flake.nix`/`flake.lock`, hello-world `py_binary //finder:bot` + one `py_test`, Determinate Nix + Bazelisk on dev container (overlay) and later the box. *Exit: `bazel test //...` green on a machine with nothing but Bazelisk/Nix/Docker installed; lockfile-freshness check passes.*

**M1 — Signal spine.** `scripts/setup_signal.py` (§4.7) takes the bot from bare container to registered, hardened, group-created, backed-up; then the allowlisted listener, Claude agent loop with config tools + SQLite settings/queries, first-run onboarding. *Exit: you create and edit the first search query entirely from Signal, and `status` answers correctly.*

**M2 — Craigslist end-to-end (no ML).** CL fetcher, dedup store, stage-0 filters, scheduler with jitter, match posts + heartbeat in the group, `run_now`. *Exit: a new cheap couch listing produces a group ping within one cadence period; "check every 2 hours" changes the live schedule.* Already a useful product.

**M3 — Aesthetic scoring.** Reference images via Signal attachments (+ optional gallery-dl board sync), CLIP scorer, Claude judge with reasons in pings, `list_recent` near-miss review, `mark_feedback`. *Exit: pings are mostly things you'd actually click; near-miss review confirms sensible score ordering.*

**M4 — Facebook Marketplace.** Playwright fetcher with persistent profile, checkpoint detection + circuit breaker + group notification on lockout. Timeboxed; fallback is an Apify actor behind the same interface. *Exit: FBM listings flow for ≥1 week without account trouble.*

**M5 — Polish.** Repost detection tuning, signal-data volume backup, systemd/compose restart hardening, README with the registration runbook.

## 8. Remaining setup inputs (needed at M1, not before)

1. The Google Voice number for the bot, and your Signal number for the allowlist.
2. An Anthropic API key on the home box.
3. Box provisioning (M0): Docker, Bazelisk, and Nix via the Determinate installer — everything else arrives hermetically through the build.
4. One-time interactive steps on the box: Signal registration captcha (M1), and the initial Facebook login into the Playwright profile (M4).
