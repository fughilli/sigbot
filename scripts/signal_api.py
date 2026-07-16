"""Native (dockerless) runner for signal-cli-rest-api — the in-container
equivalent of `docker compose up signal-api`.

  scripts/signal_api.py start [--mode normal|json-rpc]
  scripts/signal_api.py stop | restart | status

Replicates what the docker image's entrypoint does:
- MODE=normal: run signal-cli-rest-api alone (it shells out to signal-cli
  per request; registration endpoints work in this mode).
- MODE=json-rpc: write jsonrpc2.yml (account->port map the REST api reads),
  run `signal-cli daemon --tcp 127.0.0.1:6001` plus the REST api.

Binaries: preferred source is the Nix flake (`nix build .#signal-services -o
.nix-services`); fallback is the curl installer's `.deps/` prefix
(scripts/install_native_services.sh) for containers whose base image lacks
nix — /nix/store does NOT survive a container relaunch, but the workspace
does. Pidfiles/logs live in data/signal-api/. Stdlib only — runnable as
`python3 scripts/signal_api.py` with no Bazel in the loop.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "signal-cli"
RUN = REPO / "data" / "signal-api"
DAEMON_TCP_PORT = 6001
API_PORT = 8080

SERVICES = ("signal-cli-daemon", "signal-cli-rest-api")


def resolve_binaries() -> dict | None:
    """Nix build result first, curl-installer .deps fallback second."""
    nix = REPO / ".nix-services" / "bin"
    if (nix / "signal-cli-rest-api").exists():
        return {"signal_cli": nix / "signal-cli",
                "rest_api": nix / "signal-cli-rest-api", "env": {}}
    deps = REPO / ".deps"
    if (deps / "bin" / "signal-cli-rest-api").exists():
        return {"signal_cli": deps / "signal-cli" / "bin" / "signal-cli",
                "rest_api": deps / "bin" / "signal-cli-rest-api",
                "env": {"JAVA_HOME": str(deps / "jre")}}
    return None


def _pidfile(name: str) -> pathlib.Path:
    return RUN / f"{name}.pid"


def _alive(name: str) -> int | None:
    try:
        pid = int(_pidfile(name).read_text())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def _spawn(name: str, cmd: list[str], env: dict) -> None:
    log = (RUN / f"{name}.log").open("ab")
    proc = subprocess.Popen(
        cmd, stdout=log, stderr=subprocess.STDOUT,
        env=env, start_new_session=True, cwd=REPO,
    )
    _pidfile(name).write_text(str(proc.pid))
    print(f"  {name}: pid {proc.pid} (log: {RUN}/{name}.log)")


def _env(binaries: dict) -> dict:
    env = dict(os.environ)
    env.update(binaries["env"])
    env["PATH"] = f"{binaries['rest_api'].parent}:{env.get('PATH', '')}"
    return env


def _wait_api(timeout_s: int = 60) -> bool:
    for _ in range(timeout_s):
        try:
            urllib.request.urlopen(f"http://localhost:{API_PORT}/v1/about", timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def start(mode: str) -> None:
    binaries = resolve_binaries()
    if binaries is None:
        sys.exit("binaries missing — run `nix build .#signal-services -o .nix-services`"
                 " or scripts/install_native_services.sh")
    RUN.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    if any(_alive(s) for s in SERVICES):
        sys.exit("already running (use restart)")

    env = _env(binaries)
    env["MODE"] = mode
    print(f"starting signal api natively, MODE={mode}")
    if mode == "json-rpc":
        # Account->daemon-port map the REST api reads (see jsonrpc2-helper.go);
        # <multi-account> = one daemon serves every registered account.
        (DATA / "jsonrpc2.yml").write_text(
            f'config:\n  "<multi-account>":\n    tcp_port: {DAEMON_TCP_PORT}\n'
        )
        _spawn(
            "signal-cli-daemon",
            [str(binaries["signal_cli"]), "--output=json",
             "--config", str(DATA), "daemon", "--tcp", f"127.0.0.1:{DAEMON_TCP_PORT}"],
            env,
        )
    _spawn(
        "signal-cli-rest-api",
        [str(binaries["rest_api"]), f"-signal-cli-config={DATA}"],
        env,
    )
    if not _wait_api():
        sys.exit(f"API did not come up — check {RUN}/signal-cli-rest-api.log")
    print(f"up: http://localhost:{API_PORT} (mode={mode})")


def stop() -> None:
    for name in SERVICES:
        pid = _alive(name)
        if pid:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            print(f"  stopped {name} (pid {pid})")
        _pidfile(name).unlink(missing_ok=True)


def status() -> None:
    for name in SERVICES:
        pid = _alive(name)
        print(f"  {name}: {'running pid ' + str(pid) if pid else 'stopped'}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "stop", "restart", "status"])
    p.add_argument("--mode", choices=["normal", "json-rpc"], default="json-rpc")
    args = p.parse_args()
    if args.action == "start":
        start(args.mode)
    elif args.action == "stop":
        stop()
    elif args.action == "restart":
        stop()
        time.sleep(2)
        start(args.mode)
    else:
        status()


if __name__ == "__main__":
    main()
