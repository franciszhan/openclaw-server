from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CoordinatorConfig:
    state_root: Path
    relay_command: list[str]
    coordinator_slack_user_id: str | None
    allowed_public_channel_ids: list[str]
    request_timeout_seconds: int
    intent_extractor_model: str
    intent_extractor_api_key_env: str
    intent_extractor_timeout_seconds: int
    slack_bot_token: str | None
    slack_app_token: str | None
    allow_self_requests_for_testing: bool
    slack_bot_token_env: str | None = None
    slack_app_token_env: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoordinatorConfig":
        slack_bot_token_env = (
            str(data["slack_bot_token_env"]) if data.get("slack_bot_token_env") else None
        )
        slack_app_token_env = (
            str(data["slack_app_token_env"]) if data.get("slack_app_token_env") else None
        )
        return cls(
            state_root=Path(data["state_root"]),
            relay_command=[str(value) for value in data["relay_command"]],
            coordinator_slack_user_id=(
                str(data["coordinator_slack_user_id"])
                if data.get("coordinator_slack_user_id")
                else None
            ),
            allowed_public_channel_ids=[
                str(value) for value in data.get("allowed_public_channel_ids", [])
            ],
            request_timeout_seconds=int(data.get("request_timeout_seconds", 60)),
            intent_extractor_model=str(data.get("intent_extractor_model", "gpt-5-nano")),
            intent_extractor_api_key_env=str(
                data.get("intent_extractor_api_key_env", "OPENAI_API_KEY")
            ),
            intent_extractor_timeout_seconds=int(
                data.get("intent_extractor_timeout_seconds", 15)
            ),
            slack_bot_token=(
                str(data["slack_bot_token"])
                if data.get("slack_bot_token")
                else (os.getenv(slack_bot_token_env) if slack_bot_token_env else None)
            ),
            slack_app_token=(
                str(data["slack_app_token"])
                if data.get("slack_app_token")
                else (os.getenv(slack_app_token_env) if slack_app_token_env else None)
            ),
            allow_self_requests_for_testing=bool(data.get("allow_self_requests_for_testing", False)),
            slack_bot_token_env=slack_bot_token_env,
            slack_app_token_env=slack_app_token_env,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in data.items()
        }

    @property
    def requests_dir(self) -> Path:
        return self.state_root / "requests"

    @property
    def directory_path(self) -> Path:
        return self.state_root / "directory.json"

    @property
    def audit_log_path(self) -> Path:
        return self.state_root / "audit.jsonl"


def load_coordinator_config(path: Path) -> CoordinatorConfig:
    with path.open("r", encoding="utf-8") as handle:
        return CoordinatorConfig.from_dict(json.load(handle))
