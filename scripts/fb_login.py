"""One-time interactive Facebook login for the Marketplace fetcher.

Opens a real (headed) Chromium against the persistent profile dir; you log in
by hand, the session cookies persist, and the bot's headless fetcher reuses
them. Re-run whenever Facebook expires the session or the circuit breaker
reports a checkpoint.

Prereqs (the bot's systemd unit sets these too):
  nix build .#playwright-browsers -o .playwright-browsers
  export PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright-browsers
  export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
Run headed on a machine with a display, or over X-forwarding / VNC on the box.

  python3 scripts/fb_login.py [--profile .fb-profile]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib


async def main(profile_dir: str) -> None:
    from playwright.async_api import async_playwright

    pathlib.Path(profile_dir).mkdir(parents=True, exist_ok=True)
    if "BUILD_WORKING_DIRECTORY" in os.environ:
        os.chdir(os.environ["BUILD_WORKING_DIRECTORY"])

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            profile_dir, headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com/login")
        print("\n=== Log in to Facebook in the browser window.")
        print("    Complete any 2FA / checkpoint until you see your feed or Marketplace.")
        print("    Then press Enter here to save the session and exit.")
        input("    Waiting... ")
        # Touch marketplace so any marketplace-specific cookies are set too.
        try:
            await page.goto("https://www.facebook.com/marketplace/", timeout=30_000)
            await asyncio.sleep(3)
        except Exception:
            pass
        await context.close()
    print(f"\nSession saved to {profile_dir}/ — the bot can now use Facebook Marketplace.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=".fb-profile")
    args = ap.parse_args()
    asyncio.run(main(args.profile))
