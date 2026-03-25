from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from openclaw_coordinator.config import CoordinatorConfig
from openclaw_coordinator.slack_transport import SlackSocketModeRunner


class FakeApi:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def post_message(self, channel: str, text: str, *, thread_ts=None, blocks=None):
        self.messages.append(
            {
                "channel": channel,
                "text": text,
                "thread_ts": thread_ts,
                "blocks": blocks,
            }
        )
        return {}


class FakeService:
    def __init__(self) -> None:
        self.submitted_events: list[dict[str, object]] = []

    def submit_slack_request(self, event: dict[str, object]) -> dict[str, object]:
        self.submitted_events.append(event)
        return {"request": {"request_id": "req-1", "status": "pending_owner_approval"}, "actions": []}


def example_config() -> CoordinatorConfig:
    return CoordinatorConfig(
        state_root=Path("/tmp/coordinator-state"),
        relay_command=["openclaw-hostctl", "shared-access", "execute", "{owner_vm_user_id}"],
        coordinator_slack_user_id="UCOORD",
        allowed_public_channel_ids=["CROLLOUT"],
        request_timeout_seconds=180,
        intent_extractor_model="gpt-5-nano",
        intent_extractor_api_key_env="OPENAI_API_KEY",
        intent_extractor_timeout_seconds=15,
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        allow_self_requests_for_testing=False,
        dm_test_owner_slack_user_id=None,
    )


class SlackTransportRolloutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.runner = SlackSocketModeRunner(example_config(), self.service)  # type: ignore[arg-type]
        self.runner.api = FakeApi()  # type: ignore[assignment]

    def test_disallowed_public_channel_gets_denial(self) -> None:
        self.runner._handle_events_api(
            {
                "event_id": "evt-1",
                "event": {
                    "type": "app_mention",
                    "user": "UREQUEST",
                    "channel": "COTHER",
                    "ts": "123.456",
                    "text": "<@UCOORD> <@UOWNER> latest info on 1money",
                },
            }
        )
        self.assertEqual(self.service.submitted_events, [])
        self.assertEqual(len(self.runner.api.messages), 1)
        self.assertIn("only accepts new requests in <#CROLLOUT>", self.runner.api.messages[0]["text"])

    def test_allowed_public_channel_submits_request(self) -> None:
        self.runner._handle_events_api(
            {
                "event_id": "evt-2",
                "event": {
                    "type": "app_mention",
                    "user": "UREQUEST",
                    "channel": "CROLLOUT",
                    "ts": "123.456",
                    "text": "<@UCOORD> <@UOWNER> latest info on 1money",
                },
            }
        )
        self.assertEqual(len(self.service.submitted_events), 1)
        self.assertEqual(self.service.submitted_events[0]["entrypoint"], "public_thread")
        self.assertEqual(self.runner.api.messages, [])

    def test_dm_non_command_is_denied_for_new_requests(self) -> None:
        self.runner._handle_events_api(
            {
                "event_id": "evt-3",
                "event": {
                    "type": "message",
                    "channel_type": "im",
                    "user": "UREQUEST",
                    "channel": "D123",
                    "ts": "123.456",
                    "text": "can you look up emails about 1money?",
                },
            }
        )
        self.assertEqual(self.service.submitted_events, [])
        self.assertEqual(len(self.runner.api.messages), 1)
        self.assertIn("DMs are only used for approvals and review", self.runner.api.messages[0]["text"])

    def test_config_can_read_slack_tokens_from_env(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "BOT_TOKEN_ENV": "xoxb-test",
                "APP_TOKEN_ENV": "xapp-test",
            },
            clear=False,
        ):
            config = CoordinatorConfig.from_dict(
                {
                    "state_root": "/tmp/coordinator-state",
                    "relay_command": ["openclaw-hostctl", "shared-access", "execute", "{owner_vm_user_id}"],
                    "allowed_public_channel_ids": ["CROLLOUT"],
                    "request_timeout_seconds": 180,
                    "slack_bot_token_env": "BOT_TOKEN_ENV",
                    "slack_app_token_env": "APP_TOKEN_ENV",
                }
            )
        self.assertEqual(config.slack_bot_token, "xoxb-test")
        self.assertEqual(config.slack_app_token, "xapp-test")


if __name__ == "__main__":
    unittest.main()
