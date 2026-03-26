from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_host_config
from .hostctl import HostController


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw host lifecycle control")
    parser.add_argument(
        "--config",
        default="/etc/openclaw/host-config.json",
        type=Path,
        help="path to the host configuration file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-layout")
    subparsers.add_parser("validate-config")

    provision = subparsers.add_parser("provision")
    provision.add_argument("user_id")
    provision.add_argument("--display-name")
    provision.add_argument("--user-config", type=Path)
    provision.add_argument("--disk-size-gib", type=int)

    activate = subparsers.add_parser("activate-user")
    activate.add_argument("user_id")
    activate.add_argument("--manifest", type=Path)
    activate.add_argument("--force", action="store_true")
    activate.add_argument("--restart", action="store_true")

    start = subparsers.add_parser("start")
    start.add_argument("user_id")

    stop = subparsers.add_parser("stop")
    stop.add_argument("user_id")

    status = subparsers.add_parser("status")
    status.add_argument("user_id", nargs="?")

    snapshot = subparsers.add_parser("snapshot")
    snapshot_subparsers = snapshot.add_subparsers(dest="snapshot_command", required=True)

    snapshot_create = snapshot_subparsers.add_parser("create")
    snapshot_create.add_argument("user_id")
    snapshot_create.add_argument("label")

    snapshot_list = snapshot_subparsers.add_parser("list")
    snapshot_list.add_argument("user_id")

    snapshot_restore = snapshot_subparsers.add_parser("restore")
    snapshot_restore.add_argument("user_id")
    snapshot_restore.add_argument("snapshot_name")
    snapshot_restore.add_argument("--force", action="store_true")

    runtime = subparsers.add_parser("runtime")
    runtime_subparsers = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_prepare = runtime_subparsers.add_parser("prepare")
    runtime_prepare.add_argument("user_id")
    runtime_cleanup = runtime_subparsers.add_parser("cleanup")
    runtime_cleanup.add_argument("user_id")

    shared_access = subparsers.add_parser("shared-access")
    shared_access_subparsers = shared_access.add_subparsers(
        dest="shared_access_command",
        required=True,
    )
    shared_access_execute = shared_access_subparsers.add_parser("execute")
    shared_access_execute.add_argument("user_id")
    shared_access_execute.add_argument("--timeout-seconds", type=int, default=60)

    google_auth = subparsers.add_parser("google-auth")
    google_auth_subparsers = google_auth.add_subparsers(
        dest="google_auth_command",
        required=True,
    )
    google_auth_start = google_auth_subparsers.add_parser("start")
    google_auth_start.add_argument("user_id")
    google_auth_finish = google_auth_subparsers.add_parser("finish")
    google_auth_finish.add_argument("user_id")
    google_auth_finish.add_argument("--callback-url", required=True)
    google_auth_status = google_auth_subparsers.add_parser("status")
    google_auth_status.add_argument("user_id")
    google_auth_broker = google_auth_subparsers.add_parser("broker")
    google_auth_broker.add_argument("user_id")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_host_config(args.config)
    controller = HostController(config)

    if args.command == "init-layout":
        controller.init_layout()
        print(f"initialized {config.storage_root}")
        return 0

    if args.command == "validate-config":
        messages = controller.validate()
        if messages:
            for message in messages:
                print(f"ERROR: {message}")
            return 1
        print("configuration looks valid")
        return 0

    if args.command == "provision":
        record = controller.provision_user(
            args.user_id,
            display_name=args.display_name,
            user_config_path=args.user_config,
            disk_size_gib=args.disk_size_gib,
        )
        print(json.dumps(record.to_dict(), indent=2))
        return 0

    if args.command == "activate-user":
        result = controller.activate_user(
            args.user_id,
            manifest_path=args.manifest,
            force=args.force,
            restart=args.restart,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "start":
        controller.start_user(args.user_id)
        print(f"started {args.user_id}")
        return 0

    if args.command == "stop":
        controller.stop_user(args.user_id)
        print(f"stopped {args.user_id}")
        return 0

    if args.command == "status":
        print(json.dumps(controller.status(args.user_id), indent=2))
        return 0

    if args.command == "snapshot":
        if args.snapshot_command == "create":
            path = controller.create_snapshot(args.user_id, args.label)
            print(path)
            return 0
        if args.snapshot_command == "list":
            snapshots = [str(path.name) for path in controller.list_snapshots(args.user_id)]
            print(json.dumps(snapshots, indent=2))
            return 0
        if args.snapshot_command == "restore":
            path = controller.restore_snapshot(args.user_id, args.snapshot_name, force=args.force)
            print(path)
            return 0

    if args.command == "runtime":
        if args.runtime_command == "prepare":
            controller.runtime_prepare(args.user_id)
            print(f"prepared runtime for {args.user_id}")
            return 0
        if args.runtime_command == "cleanup":
            controller.runtime_cleanup(args.user_id)
            print(f"cleaned runtime for {args.user_id}")
            return 0

    if args.command == "shared-access":
        if args.shared_access_command == "execute":
            payload = json.load(sys.stdin)
            result = controller.execute_shared_access(
                args.user_id,
                payload,
                timeout_seconds=args.timeout_seconds,
            )
            print(json.dumps(result, indent=2))
            return 0

    if args.command == "google-auth":
        if args.google_auth_command == "start":
            print(controller.google_auth_start(args.user_id))
            return 0
        if args.google_auth_command == "finish":
            print(json.dumps(controller.google_auth_finish(args.user_id, args.callback_url), indent=2))
            return 0
        if args.google_auth_command == "status":
            print(json.dumps(controller.google_auth_status(args.user_id), indent=2))
            return 0
        if args.google_auth_command == "broker":
            original_command = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
            if not original_command:
                parser.error("google-auth broker requires SSH_ORIGINAL_COMMAND")
            return controller.google_auth_broker(args.user_id, original_command)

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
