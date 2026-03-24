from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import load_coordinator_config
from .models import DirectoryEntry
from .service import CoordinatorService, SubprocessRelayExecutor
from .store import CoordinatorStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw shared-access coordinator control")
    parser.add_argument(
        "--config",
        default="/etc/openclaw/coordinator-config.json",
        type=Path,
        help="path to the coordinator configuration file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-state")
    subparsers.add_parser("serve-slack")

    directory = subparsers.add_parser("directory")
    directory_subparsers = directory.add_subparsers(dest="directory_command", required=True)
    directory_upsert = directory_subparsers.add_parser("upsert")
    directory_upsert.add_argument("--manifest", type=Path, required=True)
    directory_list = directory_subparsers.add_parser("list")

    request = subparsers.add_parser("request")
    request_subparsers = request.add_subparsers(dest="request_command", required=True)
    request_submit = request_subparsers.add_parser("submit")
    request_submit.add_argument("--event", type=Path, required=True)
    request_show = request_subparsers.add_parser("show")
    request_show.add_argument("request_id")
    request_owner = request_subparsers.add_parser("owner-decision")
    request_owner.add_argument("request_id")
    request_owner.add_argument("--owner", required=True)
    request_owner.add_argument("--decision", choices=("approve", "reject"), required=True)
    request_review = request_subparsers.add_parser("review-decision")
    request_review.add_argument("request_id")
    request_review.add_argument("--owner", required=True)
    request_review.add_argument("--decision", choices=("publish", "cancel"), required=True)
    request_draft = request_subparsers.add_parser("draft-from")
    request_draft.add_argument("request_id")
    request_draft.add_argument("--requester", required=True)
    request_draft.add_argument("--source-event-id", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_coordinator_config(args.config)
    store = CoordinatorStore(config.state_root)
    service = CoordinatorService(config, store, SubprocessRelayExecutor(config))

    if args.command == "init-state":
        service.init_state()
        print(config.state_root)
        return 0

    if args.command == "serve-slack":
        from .slack_transport import SlackSocketModeRunner

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        SlackSocketModeRunner(config, service).run_forever()
        return 0

    if args.command == "directory":
        if args.directory_command == "list":
            entries = [entry.to_dict() for entry in store.load_directory().values()]
            print(json.dumps(entries, indent=2))
            return 0
        if args.directory_command == "upsert":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            shared_access = manifest.get("shared_access", {})
            if not isinstance(shared_access, dict):
                raise ValueError("manifest shared_access must be a JSON object")
            entry = DirectoryEntry(
                slack_user_id=str(manifest["slack_user_id"]),
                vm_user_id=str(manifest["user_id"]),
                display_name=str(manifest.get("display_name", manifest["user_id"])),
                vm_address=str(shared_access["vm_address"]) if shared_access.get("vm_address") else None,
                opt_in=bool(shared_access.get("opt_in", False)),
                shared_capabilities=[str(value) for value in shared_access.get("capabilities", [])],
            )
            print(json.dumps(service.upsert_directory_entry(entry).to_dict(), indent=2))
            return 0

    if args.command == "request":
        if args.request_command == "submit":
            event = json.loads(args.event.read_text(encoding="utf-8"))
            print(json.dumps(service.submit_slack_request(event), indent=2))
            return 0
        if args.request_command == "show":
            print(json.dumps(store.load_request(args.request_id).to_dict(), indent=2))
            return 0
        if args.request_command == "owner-decision":
            print(
                json.dumps(
                    service.record_owner_decision(
                        args.request_id,
                        owner_slack_user_id=args.owner,
                        decision=args.decision,
                    ),
                    indent=2,
                )
            )
            return 0
        if args.request_command == "review-decision":
            print(
                json.dumps(
                    service.record_owner_review(
                        args.request_id,
                        owner_slack_user_id=args.owner,
                        decision=args.decision,
                    ),
                    indent=2,
                )
            )
            return 0
        if args.request_command == "draft-from":
            print(
                json.dumps(
                    service.create_draft_request(
                        args.request_id,
                        requester_slack_user_id=args.requester,
                        source_event_id=args.source_event_id,
                    ),
                    indent=2,
                )
            )
            return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
