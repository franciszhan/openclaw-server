from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


REQUEST_STATUSES = {
    "pending_owner_approval",
    "owner_approved",
    "owner_rejected",
    "executing",
    "owner_review_pending",
    "published",
    "failed",
    "expired",
}

REQUEST_MODES = {"read_only", "draft_intro"}


@dataclass(frozen=True)
class DirectoryEntry:
    slack_user_id: str
    vm_user_id: str
    display_name: str
    vm_address: str | None
    opt_in: bool
    shared_capabilities: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DirectoryEntry":
        return cls(
            slack_user_id=str(data["slack_user_id"]),
            vm_user_id=str(data["vm_user_id"]),
            display_name=str(data.get("display_name", data["vm_user_id"])),
            vm_address=str(data["vm_address"]) if data.get("vm_address") else None,
            opt_in=bool(data.get("opt_in", False)),
            shared_capabilities=[str(value) for value in data.get("shared_capabilities", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoordinatorAction:
    kind: str
    request_id: str
    text: str
    channel_id: str | None = None
    thread_ts: str | None = None
    slack_user_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedRequest:
    owner_slack_user_id: str
    action_type: str
    mode: str
    entity_name: str
    entity_company: str | None
    purpose: str
    raw_text: str


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    source_event_id: str
    requester_slack_user_id: str
    requester_vm_user_id: str
    owner_slack_user_id: str
    owner_vm_user_id: str
    action_type: str
    mode: str
    entity_name: str
    entity_company: str | None
    purpose: str
    status: str
    public_channel_id: str
    public_thread_ts: str
    raw_text: str
    created_at: str
    updated_at: str
    parent_request_id: str | None = None
    owner_decided_at: str | None = None
    owner_reviewed_at: str | None = None
    published_at: str | None = None
    failure_reason: str | None = None
    result: dict[str, Any] | None = None
    result_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RequestRecord":
        status = str(data["status"])
        if status not in REQUEST_STATUSES:
            raise ValueError(f"unknown request status: {status}")
        mode = str(data["mode"])
        if mode not in REQUEST_MODES:
            raise ValueError(f"unknown request mode: {mode}")
        return cls(
            request_id=str(data["request_id"]),
            source_event_id=str(data["source_event_id"]),
            requester_slack_user_id=str(data["requester_slack_user_id"]),
            requester_vm_user_id=str(data["requester_vm_user_id"]),
            owner_slack_user_id=str(data["owner_slack_user_id"]),
            owner_vm_user_id=str(data["owner_vm_user_id"]),
            action_type=str(data["action_type"]),
            mode=mode,
            entity_name=str(data["entity_name"]),
            entity_company=str(data["entity_company"]) if data.get("entity_company") else None,
            purpose=str(data["purpose"]),
            status=status,
            public_channel_id=str(data["public_channel_id"]),
            public_thread_ts=str(data["public_thread_ts"]),
            raw_text=str(data["raw_text"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            parent_request_id=str(data["parent_request_id"]) if data.get("parent_request_id") else None,
            owner_decided_at=str(data["owner_decided_at"]) if data.get("owner_decided_at") else None,
            owner_reviewed_at=str(data["owner_reviewed_at"]) if data.get("owner_reviewed_at") else None,
            published_at=str(data["published_at"]) if data.get("published_at") else None,
            failure_reason=str(data["failure_reason"]) if data.get("failure_reason") else None,
            result=dict(data["result"]) if data.get("result") else None,
            result_metadata=dict(data.get("result_metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
