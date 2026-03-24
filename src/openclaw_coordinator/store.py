from __future__ import annotations

import json
from pathlib import Path

from .models import DirectoryEntry, RequestRecord


class CoordinatorStore:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.requests_dir = state_root / "requests"
        self.directory_path = state_root / "directory.json"
        self.audit_log_path = state_root / "audit.jsonl"

    def init_layout(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        if not self.directory_path.exists():
            self.directory_path.write_text("{}\n", encoding="utf-8")
        self.audit_log_path.touch(exist_ok=True)

    def load_directory(self) -> dict[str, DirectoryEntry]:
        if not self.directory_path.exists():
            return {}
        with self.directory_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        return {key: DirectoryEntry.from_dict(value) for key, value in raw.items()}

    def save_directory(self, entries: dict[str, DirectoryEntry]) -> None:
        payload = {key: entry.to_dict() for key, entry in sorted(entries.items())}
        self.directory_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def upsert_directory_entry(self, entry: DirectoryEntry) -> None:
        entries = self.load_directory()
        entries[entry.slack_user_id] = entry
        self.save_directory(entries)

    def get_directory_entry(self, slack_user_id: str) -> DirectoryEntry | None:
        return self.load_directory().get(slack_user_id)

    def request_path(self, request_id: str) -> Path:
        return self.requests_dir / f"{request_id}.json"

    def save_request(self, record: RequestRecord) -> None:
        self.request_path(record.request_id).write_text(
            json.dumps(record.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )

    def load_request(self, request_id: str) -> RequestRecord:
        with self.request_path(request_id).open("r", encoding="utf-8") as handle:
            return RequestRecord.from_dict(json.load(handle))

    def list_requests(self) -> list[RequestRecord]:
        if not self.requests_dir.exists():
            return []
        return [
            RequestRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.requests_dir.glob("*.json"))
        ]

    def find_by_source_event_id(self, source_event_id: str) -> RequestRecord | None:
        for record in self.list_requests():
            if record.source_event_id == source_event_id:
                return record
        return None

    def append_audit_event(self, event: dict[str, object]) -> None:
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
