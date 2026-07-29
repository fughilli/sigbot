#!/usr/bin/env bash
# sigbot installer: pulls the prebuilt image (sigbot + bundled
# signal-cli-rest-api in one container), commissions a data dir (config +
# secrets), and installs the `sigbot-d` command. It does NOT start anything —
# that's `sigbot-d <data-dir>`. Safe to re-run: commissioning is skipped if
# the data dir already has config.
#
#   curl -fsSL https://raw.githubusercontent.com/fughilli/sigbot/master/deploy/install.sh | bash
#
# Env overrides: SIGBOT_HOME (the data dir, default ~/.config/sigbot), SIGBOT_IMAGE,
#   SIGBOT_BIN_DIR (where sigbot-d goes; default /usr/local/bin if writable,
#   else ~/.local/bin).
set -euo pipefail

SIGBOT_HOME="${SIGBOT_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/sigbot}"
SIGBOT_IMAGE="${SIGBOT_IMAGE:-ghcr.io/fughilli/sigbot:latest}"
RAW_BASE="${SIGBOT_RAW_BASE:-https://raw.githubusercontent.com/fughilli/sigbot/master/deploy}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker is required (https://docs.docker.com/engine/install/)"

mkdir -p "$SIGBOT_HOME/signal-cli"
cd "$SIGBOT_HOME"

# -- commissioning: config + secrets, generated once ---------------------------

if [[ -f sigbot.yaml && -f .env ]]; then
  say "already commissioned ($SIGBOT_HOME) — keeping existing config/secrets"
else
  say "commissioning sigbot in $SIGBOT_HOME"
  # `curl | bash` leaves stdin on the pipe — prompt on the terminal instead
  if [[ ! -t 0 ]]; then
    [[ -r /dev/tty ]] || fail "no terminal for prompts; run the script directly"
    exec < /dev/tty
  fi
  read -rp "Bot Signal number (E.164, e.g. +15551234567): " BOT_NUMBER
  [[ "$BOT_NUMBER" =~ ^\+[0-9]{7,15}$ ]] || fail "that doesn't look like an E.164 number"
  read -rp "Bot display name [Botsy]: " BOT_NAME; BOT_NAME="${BOT_NAME:-Botsy}"
  read -rp "Anthropic API key (sk-ant-...): " ANTHROPIC_KEY
  [[ -n "$ANTHROPIC_KEY" ]] || fail "an Anthropic API key is required"
  read -rp "Dashboard admin username [admin]: " ADMIN_USER; ADMIN_USER="${ADMIN_USER:-admin}"
  read -rsp "Dashboard admin password [generate]: " ADMIN_PASS; echo
  if [[ -z "$ADMIN_PASS" ]]; then
    ADMIN_PASS="$(head -c 12 /dev/urandom | base64 | tr -d '/+=' )"
    GENERATED=1
  fi

  cat > sigbot.yaml <<EOF
# sigbot static config (commissioned $(date -u +%F) by install.sh).
# Services (group personas), API keys, and further admins are managed in the
# dashboard — not in this file. This dir is mounted at /data in the container.
signal:
  api_url: http://127.0.0.1:8080   # bundled in the same container
  bot_number: "$BOT_NUMBER"
bot_name: $BOT_NAME
db_path: sigbot.db
anthropic_api_key_env: ANTHROPIC_API_KEY
default_model: claude-sonnet-4-6
api:
  host: 0.0.0.0
  port: 8100
EOF

  cat > .env <<EOF
# sigbot secrets (chmod 600; never commit). Reset the admin password with:
#   docker exec <container> /sigbot/admin --config /data/sigbot.yaml set-password $ADMIN_USER
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
SIGBOT_ADMIN_USER=$ADMIN_USER
SIGBOT_ADMIN_PASSWORD=$ADMIN_PASS
EOF
  chmod 600 .env
  say "wrote sigbot.yaml and .env (secrets, mode 600)"
  [[ -n "${GENERATED:-}" ]] && say "generated dashboard password for '$ADMIN_USER': $ADMIN_PASS (stored in .env)"
fi

# -- install the sigbot-d launcher ---------------------------------------------

BIN_DIR="${SIGBOT_BIN_DIR:-}"
if [[ -z "$BIN_DIR" ]]; then
  if [[ -w /usr/local/bin ]]; then BIN_DIR=/usr/local/bin; else BIN_DIR="$HOME/.local/bin"; fi
fi
mkdir -p "$BIN_DIR"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/null}")" 2>/dev/null && pwd || true)"
if [[ -n "$script_dir" && -f "$script_dir/sigbot-d" ]]; then
  cp "$script_dir/sigbot-d" "$BIN_DIR/sigbot-d"   # running from a checkout
else
  curl -fsSL "$RAW_BASE/sigbot-d" -o "$BIN_DIR/sigbot-d" \
    || fail "could not fetch sigbot-d from $RAW_BASE"
fi
chmod 0755 "$BIN_DIR/sigbot-d"
say "installed $BIN_DIR/sigbot-d"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "NOTE: $BIN_DIR is not on your PATH — add it, or call $BIN_DIR/sigbot-d directly" ;;
esac

say "pulling $SIGBOT_IMAGE"
docker pull "$SIGBOT_IMAGE"

cat <<EOF

sigbot is installed and commissioned (nothing is running yet).

Start it in the background:

  sigbot-d $SIGBOT_HOME

Then, if this Signal number isn't provisioned yet (fresh signal-cli/ dir), do ONE of:
  - Register it as a new account (needs an SMS/voice-capable number + captcha):
      https://github.com/bbernhard/signal-cli-rest-api/blob/master/doc/EXAMPLES.md#register-a-number
    (the API is on http://127.0.0.1:8080 while sigbot-d is running)
  - Link it as a device of an existing account: open
      http://127.0.0.1:8080/v1/qrcodelink?device_name=sigbot
    and scan the QR from Signal > Settings > Linked Devices.
Then restart:  sigbot-d stop $SIGBOT_HOME && sigbot-d $SIGBOT_HOME

Dashboard (once running): http://localhost:8100/  (login: see $SIGBOT_HOME/.env)
Manage:  sigbot-d status|logs|stop $SIGBOT_HOME
EOF
