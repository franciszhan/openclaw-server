from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from .config import CoordinatorConfig
from .intent_extractor import OpenAIIntentExtractor
from .models import CoordinatorAction, DirectoryEntry, ParsedRequest, RequestRecord
from .policy import evaluate_request_policy
from .store import CoordinatorStore


class SubprocessRelayExecutor:
    def __init__(self, config: CoordinatorConfig) -> None:
        self.config = config

    def execute(
        self,
        owner: DirectoryEntry,
        request: RequestRecord,
    ) -> dict[str, object]:
        command = [
            arg.format(
                owner_vm_user_id=owner.vm_user_id,
                owner_slack_user_id=owner.slack_user_id,
                request_id=request.request_id,
            )
            for arg in self.config.relay_command
        ]
        if (
            len(command) >= 3
            and command[0] == "openclaw-hostctl"
            and command[1] == "shared-access"
            and command[2] == "execute"
            and "--timeout-seconds" not in command
        ):
            command.extend(["--timeout-seconds", str(self.config.request_timeout_seconds)])
        payload = {
            "request_id": request.request_id,
            "requester_slack_user_id": request.requester_slack_user_id,
            "requester_vm_user_id": request.requester_vm_user_id,
            "owner_slack_user_id": request.owner_slack_user_id,
            "owner_vm_user_id": request.owner_vm_user_id,
            "action_type": request.action_type,
            "mode": request.mode,
            "entity_name": request.entity_name,
            "entity_company": request.entity_company,
            "purpose": request.purpose,
        }
        result = subprocess.run(
            command,
            check=True,
            text=True,
            input=json.dumps(payload),
            capture_output=True,
            timeout=self.config.request_timeout_seconds,
        )
        return json.loads(result.stdout)


class CoordinatorService:
    def __init__(
        self,
        config: CoordinatorConfig,
        store: CoordinatorStore,
        executor: SubprocessRelayExecutor,
        intent_extractor: OpenAIIntentExtractor | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.executor = executor
        self.intent_extractor = intent_extractor or OpenAIIntentExtractor(config)

    def init_state(self) -> None:
        self.store.init_layout()

    def upsert_directory_entry(self, entry: DirectoryEntry) -> DirectoryEntry:
        self.store.upsert_directory_entry(entry)
        return entry

    def submit_slack_request(self, event: dict[str, object]) -> dict[str, object]:
        self.store.init_layout()
        source_event_id = str(event["event_id"])
        existing = self.store.find_by_source_event_id(source_event_id)
        if existing:
            return {
                "request": existing.to_dict(),
                "actions": [],
                "duplicate": True,
            }
        entrypoint = str(event.get("entrypoint", "public_thread"))
        requester_slack_user_id = str(event["requester_slack_user_id"])
        allow_requester_as_owner = bool(self.config.allow_self_requests_for_testing)
        owner_aliases = self._owner_aliases()
        parsed = self.intent_extractor.extract(
            text=str(event["text"]),
            requester_slack_user_id=requester_slack_user_id,
            coordinator_slack_user_id=self.config.coordinator_slack_user_id,
            owner_aliases=owner_aliases,
            allow_requester_as_owner=allow_requester_as_owner,
        )
        requester = self.store.get_directory_entry(requester_slack_user_id)
        owner = self.store.get_directory_entry(parsed.owner_slack_user_id)
        policy_error = evaluate_request_policy(
            requester=requester,
            owner=owner,
            parsed_request=parsed,
            allow_self_requests=allow_requester_as_owner,
        )
        if policy_error:
            raise ValueError(policy_error)
        assert requester is not None
        assert owner is not None
        now = timestamp_now()
        record = RequestRecord(
            request_id=uuid.uuid4().hex[:12],
            source_event_id=source_event_id,
            requester_slack_user_id=requester.slack_user_id,
            requester_vm_user_id=requester.vm_user_id,
            owner_slack_user_id=owner.slack_user_id,
            owner_vm_user_id=owner.vm_user_id,
            action_type=parsed.action_type,
            mode=parsed.mode,
            entity_name=parsed.entity_name,
            entity_company=parsed.entity_company,
            purpose=parsed.purpose,
            status="pending_owner_approval",
            public_channel_id=str(event["channel_id"]),
            public_thread_ts=str(event["thread_ts"]),
            raw_text=parsed.raw_text,
            created_at=now,
            updated_at=now,
        )
        self.store.save_request(record)
        self._audit("request_submitted", record)
        actions = [
            CoordinatorAction(
                kind="public_ack",
                request_id=record.request_id,
                channel_id=record.public_channel_id,
                thread_ts=record.public_thread_ts,
                text=(
                    f"Request `{record.request_id}` queued for {owner.display_name}'s approval "
                    "for a shared email lookup."
                ),
            ),
            CoordinatorAction(
                kind="owner_dm_approval",
                request_id=record.request_id,
                slack_user_id=owner.slack_user_id,
                text=(
                    f"{requester.display_name} requested a shared email lookup for "
                    f"`{record.entity_name}`.\n\n"
                    f"Request ID: `{record.request_id}`\n"
                    f"Reply with `approve {record.request_id}` or `reject {record.request_id}`."
                ),
            ),
        ]
        return {"request": record.to_dict(), "actions": [action.to_dict() for action in actions]}

    def submit_public_request(self, event: dict[str, object]) -> dict[str, object]:
        public_event = dict(event)
        public_event.setdefault("entrypoint", "public_thread")
        return self.submit_slack_request(public_event)

    def record_owner_decision(
        self,
        request_id: str,
        *,
        owner_slack_user_id: str,
        decision: str,
    ) -> dict[str, object]:
        record = self.store.load_request(request_id)
        if record.owner_slack_user_id != owner_slack_user_id:
            raise ValueError("only the target owner can decide this request")
        if record.status != "pending_owner_approval":
            raise ValueError("request is not awaiting owner approval")
        now = timestamp_now()
        if decision == "reject":
            updated = replace(
                record,
                status="owner_rejected",
                owner_decided_at=now,
                updated_at=now,
            )
            self.store.save_request(updated)
            self._audit("owner_rejected", updated)
            actions = [
                CoordinatorAction(
                    kind="public_rejected",
                    request_id=updated.request_id,
                    channel_id=updated.public_channel_id,
                    thread_ts=updated.public_thread_ts,
                    text="The owner declined this shared email request.",
                )
            ]
            return {"request": updated.to_dict(), "actions": [action.to_dict() for action in actions]}
        if decision != "approve":
            raise ValueError("decision must be approve or reject")

        owner = self.store.get_directory_entry(record.owner_slack_user_id)
        if owner is None:
            raise ValueError("owner is no longer registered")
        approved = replace(record, status="executing", owner_decided_at=now, updated_at=now)
        self.store.save_request(approved)
        self._audit("owner_approved", approved)
        try:
            result = self.executor.execute(owner, approved)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            failure_text = extract_failure_text(error)
            user_error = classify_execution_failure(failure_text)
            failed = replace(
                approved,
                status="failed",
                failure_reason=failure_text,
                result_metadata={"user_error": user_error},
                updated_at=timestamp_now(),
            )
            self.store.save_request(failed)
            self._audit("execution_failed", failed)
            actions = [
                CoordinatorAction(
                    kind="public_failed",
                    request_id=failed.request_id,
                    channel_id=failed.public_channel_id,
                    thread_ts=failed.public_thread_ts,
                    text=user_error,
                )
            ]
            return {"request": failed.to_dict(), "actions": [action.to_dict() for action in actions]}

        reviewed = replace(
            approved,
            status="owner_review_pending",
            updated_at=timestamp_now(),
            result=result,
        )
        self.store.save_request(reviewed)
        self._audit("owner_review_pending", reviewed)
        actions = [
            CoordinatorAction(
                kind="owner_dm_review",
                request_id=reviewed.request_id,
                slack_user_id=reviewed.owner_slack_user_id,
                text=(
                    "Execution completed.\n\n"
                    f"Request ID: `{reviewed.request_id}`\n"
                    f"{format_preview_result(reviewed)}\n\n"
                    f"Reply with `publish {reviewed.request_id}` or `cancel {reviewed.request_id}`."
                ),
            )
        ]
        return {"request": reviewed.to_dict(), "actions": [action.to_dict() for action in actions]}

    def record_owner_review(
        self,
        request_id: str,
        *,
        owner_slack_user_id: str,
        decision: str,
    ) -> dict[str, object]:
        record = self.store.load_request(request_id)
        if record.owner_slack_user_id != owner_slack_user_id:
            raise ValueError("only the target owner can review this request")
        if record.status != "owner_review_pending":
            raise ValueError("request is not awaiting owner review")
        now = timestamp_now()
        if decision == "cancel":
            updated = replace(
                record,
                status="failed",
                failure_reason="owner_review_cancelled",
                owner_reviewed_at=now,
                updated_at=now,
            )
            self.store.save_request(updated)
            self._audit("owner_review_cancelled", updated)
            actions = [
                CoordinatorAction(
                    kind="public_cancelled",
                    request_id=updated.request_id,
                    channel_id=updated.public_channel_id,
                    thread_ts=updated.public_thread_ts,
                    text="The owner reviewed the result and chose not to publish it.",
                )
            ]
            return {"request": updated.to_dict(), "actions": [action.to_dict() for action in actions]}
        if decision != "publish":
            raise ValueError("decision must be publish or cancel")
        published = replace(
            record,
            status="published",
            owner_reviewed_at=now,
            published_at=now,
            updated_at=now,
        )
        self.store.save_request(published)
        self._audit("published", published)
        actions = [
            CoordinatorAction(
                kind="public_published",
                request_id=published.request_id,
                channel_id=published.public_channel_id,
                thread_ts=published.public_thread_ts,
                text=format_public_result(published),
            )
        ]
        return {"request": published.to_dict(), "actions": [action.to_dict() for action in actions]}

    def _audit(self, event_type: str, record: RequestRecord) -> None:
        self.store.append_audit_event(
            {
                "event_type": event_type,
                "request_id": record.request_id,
                "status": record.status,
                "requester_slack_user_id": record.requester_slack_user_id,
                "owner_slack_user_id": record.owner_slack_user_id,
                "action_type": record.action_type,
                "mode": record.mode,
                "timestamp": timestamp_now(),
            }
        )

    def _owner_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for entry in self.store.load_directory().values():
            for alias in {entry.display_name, entry.vm_user_id}:
                normalized = " ".join(str(alias).strip().split())
                if normalized:
                    aliases.setdefault(normalized, entry.slack_user_id)
        return aliases


def format_public_result(record: RequestRecord) -> str:
    result = record.result or {}
    lines = [
        f"Request `{record.request_id}` completed for `{record.entity_name}`.",
        "",
        f"*Context summary*\n{result.get('summary_details', '')}",
        "",
        f"*Business update*\n{result.get('business_update', '')}",
        "",
        f"*Best point of contact*\n{result.get('best_point_of_contact', '')}",
    ]
    references = result.get("references", [])
    if references:
        lines.extend(["", "*References*"])
        for reference in references:
            if not isinstance(reference, dict):
                continue
            subject = reference.get("subject") or reference.get("message_id") or "message"
            sender = reference.get("sender") or "unknown sender"
            date = reference.get("date") or "unknown date"
            lines.append(f"- {subject} ({sender}, {date})")
    return "\n".join(lines)


def format_preview_result(record: RequestRecord) -> str:
    result = record.result or {}
    lines = [
        f"*Context summary*\n{result.get('summary_details', '')}",
        "",
        f"*Business update*\n{result.get('business_update', '')}",
        "",
        f"*Best point of contact*\n{result.get('best_point_of_contact', '')}",
    ]
    references = result.get("references", [])
    if references:
        lines.extend(["", "*References*"])
        for reference in references[:3]:
            if not isinstance(reference, dict):
                continue
            subject = reference.get("subject") or reference.get("message_id") or "message"
            sender = reference.get("sender") or "unknown sender"
            date = reference.get("date") or "unknown date"
            lines.append(f"- {subject} ({sender}, {date})")
    return "\n".join(lines)


def classify_execution_failure(failure_reason: str) -> str:
    lowered = failure_reason.lower()
    if "lookup returned no references within allowed mailbox scope" in lowered:
        return (
            "The shared lookup completed, but it did not find relevant emails within the "
            "currently allowed shared mailboxes for that topic."
        )
    if "timed out" in lowered:
        return "The shared lookup took too long and timed out before producing a result."
    return "The scoped shared-access lookup failed before publication."


def extract_failure_text(error: Exception) -> str:
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()
    stdout = getattr(error, "stdout", None)
    if isinstance(stdout, str) and stdout.strip():
        return stdout.strip()
    return str(error)


def timestamp_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
