"""Privileged CLI: manage dashboard admins and mint API keys without the web
UI (e.g. headless recovery, or scripting service setup).

    bazel run //sigbot:admin -- [--config sigbot.yaml] <command> ...

Passwords are read from $SIGBOT_ADMIN_PASSWORD if set, else prompted.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sigbot import auth
from sigbot import config as config_mod
from sigbot.store import Store


def _password() -> str:
    pw = os.environ.get("SIGBOT_ADMIN_PASSWORD")
    if pw:
        return pw
    pw = getpass.getpass("password: ")
    if pw != getpass.getpass("again: "):
        sys.exit("passwords do not match")
    if len(pw) < 8:
        sys.exit("password too short (8 chars min)")
    return pw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("SIGBOT_CONFIG", "sigbot.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-admin", help="create a dashboard login")
    p.add_argument("username")
    p = sub.add_parser("set-password", help="reset a dashboard login's password")
    p.add_argument("username")
    p = sub.add_parser("remove-admin")
    p.add_argument("username")
    sub.add_parser("list-admins")

    sub.add_parser(
        "hash-password",
        help="print the PBKDF2 hash of $SIGBOT_ADMIN_PASSWORD (or prompted) — "
             "for SIGBOT_ADMIN_PASSWORD_HASH in a deployment .env; needs no config/db",
    )

    sub.add_parser("list-services")
    p = sub.add_parser("mint-key", help="mint an API key for a service")
    p.add_argument("service", help="service name")
    p.add_argument("--label", default=None, help="what this key is for")
    p = sub.add_parser("revoke-key")
    p.add_argument("key_id", type=int)

    args = parser.parse_args()
    if args.command == "hash-password":  # pure function of the password: no config/db
        print(auth.hash_password(_password()))
        return
    workdir = os.environ.get("BUILD_WORKING_DIRECTORY")
    if workdir:
        os.chdir(workdir)
    store = Store(config_mod.load(args.config).db_path)

    if args.command == "add-admin":
        if store.get_admin(args.username):
            sys.exit(f"admin {args.username!r} already exists (use set-password)")
        store.upsert_admin(args.username, auth.hash_password(_password()))
        print(f"admin {args.username!r} created")
    elif args.command == "set-password":
        if not store.get_admin(args.username):
            sys.exit(f"no admin {args.username!r}")
        store.upsert_admin(args.username, auth.hash_password(_password()))
        print(f"password updated for {args.username!r}")
    elif args.command == "remove-admin":
        if not store.delete_admin(args.username):
            sys.exit(f"no admin {args.username!r}")
        print(f"admin {args.username!r} removed")
    elif args.command == "list-admins":
        for name in store.list_admins():
            print(name)
    elif args.command == "list-services":
        for s in store.list_services():
            state = "" if s["enabled"] else "  [disabled]"
            print(f"{s['id']:>3}  {s['name']:<24} label={s['label']!r} "
                  f"group={s['group_name'] or s['group_id']}{state}")
    elif args.command == "mint-key":
        service = store.get_service_by_name(args.service)
        if not service:
            sys.exit(f"no service {args.service!r}")
        key, key_hash = auth.new_api_key()
        row = store.add_api_key(service["id"], key_hash, label=args.label)
        print(f"key id {row['id']} for service {service['name']!r} "
              "(plaintext shown once):")
        print(key)
    elif args.command == "revoke-key":
        if not store.revoke_api_key(args.key_id):
            sys.exit(f"no active key {args.key_id}")
        print(f"key {args.key_id} revoked")


if __name__ == "__main__":
    main()
