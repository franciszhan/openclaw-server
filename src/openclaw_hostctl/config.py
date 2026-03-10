from __future__ import annotations

import json
from pathlib import Path

from .models import HostConfig, UserRecord


def load_host_config(path: Path) -> HostConfig:
    with path.open("r", encoding="utf-8") as handle:
        return HostConfig.from_dict(json.load(handle))


def save_user_record(path: Path, record: UserRecord) -> None:
    path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")


def load_user_record(path: Path) -> UserRecord:
    with path.open("r", encoding="utf-8") as handle:
        return UserRecord.from_dict(json.load(handle))


def list_user_records(vm_root: Path) -> list[UserRecord]:
    if not vm_root.exists():
        return []
    records: list[UserRecord] = []
    for record_path in sorted(vm_root.glob("*/vm.json")):
        records.append(load_user_record(record_path))
    return records

