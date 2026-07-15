"""Guided, idempotent Signal bot-account creation (PLAN.md §4.7).

Run interactively on the box:  bazel run //scripts:setup_signal
Re-running is always safe: each phase checks actual state (API accounts,
container mode, backups) and skips what's already done. Only two steps are
manual: solving the registration captcha, and reading the SMS code off
Google Voice.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys
import tarfile
import time

import httpx

API = "http://localhost:8080"
CAPTCHA_URL = "https://signalcaptchas.org/registration/generate.html"
SIGNAL_DATA = pathlib.Path("data/signal-cli")
BACKUP_DIR = pathlib.Path("data/backups")


def say(msg: str) -> None:
    print(f"\n=== {msg}")


def ask(prompt: str) -> str:
    return input(f"    {prompt} ").strip()


def fail(msg: str) -> None:
    sys.exit(f"!! {msg}")


def compose(mode: str) -> None:
    """(Re)start signal-cli-rest-api in the given MODE via docker compose."""
    say(f"restarting signal-api container in MODE={mode}")
    subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "signal-api"],
        check=True,
        env={"SIGNAL_API_MODE": mode, "PATH": "/usr/bin:/usr/local/bin:/bin"},
    )
    for _ in range(30):
        try:
            httpx.get(f"{API}/v1/about", timeout=2)
            return
        except httpx.HTTPError:
            time.sleep(1)
    fail("signal-api container did not come up")


def api_mode(c: httpx.Client) -> str:
    return c.get("/v1/about").json().get("mode", "unknown")


def registered_numbers(c: httpx.Client) -> list[str]:
    r = c.get("/v1/accounts")
    return r.json() if r.status_code == 200 else []


def normalize_e164(raw: str) -> str:
    n = re.sub(r"[^\d+]", "", raw)
    if not re.fullmatch(r"\+1\d{10}", n):
        fail(f"{raw!r} is not a +1XXXXXXXXXX number")
    return n


def main() -> None:
    # Phase 0 — preflight
    say("phase 0: preflight")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        fail("docker is not available — install/start it first")
    if not pathlib.Path("docker-compose.yml").exists():
        fail("run from the repo root (docker-compose.yml not found)")

    bot = normalize_e164(ask("Bot number (the Google Voice number):"))
    user = normalize_e164(ask("Your Signal number (for the hello test):"))

    try:
        httpx.get(f"{API}/v1/about", timeout=2)
    except httpx.HTTPError:
        compose("normal")

    with httpx.Client(base_url=API, timeout=90) as c:
        # Phase 1+2 — captcha, register, verify (skipped if already registered)
        if bot in registered_numbers(c):
            say(f"{bot} is already registered — skipping registration")
        else:
            if api_mode(c) != "normal":
                compose("normal")
            say("phase 1: captcha (manual)")
            print(f"    Open {CAPTCHA_URL}")
            print("    Solve it; copy the signalcaptcha:// link the page produces.")
            captcha = ask("Paste it here:").removeprefix("signalcaptcha://")

            say("phase 2: register")
            r = c.post(f"/v1/register/{bot}", json={"captcha": captcha, "use_voice": False})
            if r.status_code == 429:
                fail(f"rate-limited by Signal; wait and re-run. body: {r.text}")
            if r.status_code >= 400 and "captcha" in r.text.lower():
                fail(f"captcha rejected (they expire in minutes) — re-run. body: {r.text}")
            r.raise_for_status()
            print("    SMS sent to the GV number (check the GV inbox / spam).")
            code = ask("Verification code (or 'voice' to retry by call):")
            if code.lower() == "voice":
                c.post(f"/v1/register/{bot}", json={"captcha": captcha, "use_voice": True}).raise_for_status()
                code = ask("Code from the voice call:")
            c.post(f"/v1/register/{bot}/verify/{code}").raise_for_status()
            say(f"{bot} registered ✓")

        # Phase 3 — hardening (best-effort; APIs vary a little across versions)
        say("phase 3: registration-lock PIN + profile")
        pin = ask("Choose a registration-lock PIN (digits, blank to skip):")
        if pin:
            r = c.post(f"/v1/accounts/{bot}/pin", json={"pin": pin})
            print(f"    PIN: {'set ✓ — store it with your secrets' if r.is_success else f'FAILED ({r.status_code}) — set it later'}")
        r = c.put(f"/v1/profiles/{bot}", json={"name": "Furniture Finder"})
        print(f"    profile name: {'set ✓' if r.is_success else f'FAILED ({r.status_code})'}")

    # Phase 4 — switch to service (json-rpc) mode
    say("phase 4: switch to json-rpc mode")
    compose("json-rpc")
    with httpx.Client(base_url=API, timeout=90) as c:
        if api_mode(c) != "json-rpc":
            fail("container did not come up in json-rpc mode")

        # Phase 5 — first contact (the bot creates the group itself on first start)
        say("phase 5: first contact")
        c.post("/v2/send", json={
            "number": bot, "recipients": [user],
            "message": "\U0001f44b Furniture Finder here — accept this message "
                       "request so I can add you to our group chat.",
        }).raise_for_status()
        print(f"    Sent. On YOUR phone: accept the message request from {bot}.")
        ask("Press enter once accepted…")

    # Phase 6 — backup
    say("phase 6: backup of signal data volume")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"signal-{stamp}.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(SIGNAL_DATA, arcname="signal-cli")
    print(f"    {dest} written. Restore: stop container, untar into {SIGNAL_DATA}, start.")
    print("    Re-backup monthly, and keep the GV number minimally active.")

    say("done — next: fill config.yaml (numbers above), export ANTHROPIC_API_KEY,")
    print("    then start the bot:  bazelisk run //finder:finder_bot")
    print("    It will create the '\U0001f6cb Furniture Finder' group and message you.")


if __name__ == "__main__":
    main()
