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


def approval_request(status: str = "pending_owner_approval") -> dict[str, object]:
    return {
        "request_id": "abc123def456",
        "source_event_id": "evt-approval",
        "requester_slack_user_id": "UREQUEST",
        "requester_vm_user_id": None,
        "owner_slack_user_id": "UOWNER",
        "owner_vm_user_id": "francis",
        "action_type": "email_intro_lookup",
        "mode": "read_only",
        "entity_name": "EDG",
        "entity_company": None,
        "purpose": "latest updates on EDG",
        "status": status,
        "response_channel_id": "DREQ",
        "response_thread_ts": "",
        "raw_text": "latest updates on EDG",
        "created_at": "2026-04-29T20:00:00Z",
        "updated_at": "2026-04-29T20:01:00Z",
        "result": {
            "answer": "EDG has new traction.",
            "supporting_context": "Two recent threads mention updates.",
            "why_these_emails": "They are the most recent relevant emails.",
            "references": [],
        },
        "result_metadata": {},
    }


def example_config() -> CoordinatorConfig:
    return CoordinatorConfig(
        state_root=Path("/tmp/coordinator-state"),
        relay_command=["openclaw-hostctl", "shared-access", "execute", "{owner_vm_user_id}"],
        coordinator_slack_user_id="UCOORD",
        request_timeout_seconds=180,
        intent_extractor_model="gpt-5-nano",
        intent_extractor_api_key_env="OPENAI_API_KEY",
        intent_extractor_timeout_seconds=15,
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        allow_self_requests_for_testing=False,
    )


class SlackTransportDmOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.runner = SlackSocketModeRunner(example_config(), self.service)  # type: ignore[arg-type]
        self.runner.api = FakeApi()  # type: ignore[assignment]

    def test_public_app_mention_sends_dm_guidance_without_submitting_request(self) -> None:
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
        self.assertEqual(self.runner.api.messages[0]["channel"], "UREQUEST")
        self.assertIn("Please DM me", self.runner.api.messages[0]["text"])

    def test_public_app_mention_never_submits_request(self) -> None:
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
        self.assertEqual(self.service.submitted_events, [])
        self.assertEqual(len(self.runner.api.messages), 1)
        self.assertEqual(self.runner.api.messages[0]["channel"], "UREQUEST")

    def test_dm_non_command_submits_new_request(self) -> None:
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
        self.assertEqual(len(self.service.submitted_events), 1)
        self.assertEqual(self.service.submitted_events[0]["entrypoint"], "dm")
        self.assertEqual(self.service.submitted_events[0]["channel_id"], "D123")
        self.assertEqual(self.service.submitted_events[0]["thread_ts"], "")
        self.assertEqual(self.runner.api.messages, [])

    def test_owner_approval_blocks_include_generated_result(self) -> None:
        blocks = self.runner._owner_approval_blocks(
            approval_request(),
            "approval text",
        )
        block_text = "\n".join(
            block.get("text", {}).get("text", "")
            for block in blocks
            if isinstance(block.get("text"), dict)
        )
        self.assertIn("Generated result awaiting approval", block_text)
        self.assertIn("EDG has new traction.", block_text)
        actions = [block for block in blocks if block.get("type") == "actions"][0]
        self.assertEqual(actions["elements"][0]["text"]["text"], "Approve & Send")

    def test_run_coordinator_action_posts_ack_before_running_lookup(self) -> None:
        class QueuedLookupService:
            def __init__(self) -> None:
                self.prepare_calls: list[str] = []

            def prepare_owner_approval(self, request_id: str):
                self.prepare_calls.append(request_id)
                request = approval_request()
                return {
                    "request": request,
                    "actions": [
                        {
                            "kind": "owner_dm_approval",
                            "request_id": request_id,
                            "slack_user_id": "UOWNER",
                            "text": "Owner approval text",
                            "channel_id": None,
                            "thread_ts": None,
                        }
                    ],
                }

        service = QueuedLookupService()
        runner = SlackSocketModeRunner(example_config(), service)  # type: ignore[arg-type]
        runner.api = FakeApi()  # type: ignore[assignment]
        result = runner._run_coordinator_action(
            lambda: {
                "lookup_queued": True,
                "request": {
                    **approval_request(status="executing"),
                    "result": None,
                },
                "actions": [
                    {
                        "kind": "requester_dm_ack",
                        "request_id": "abc123def456",
                        "channel_id": "DREQ",
                        "thread_ts": "",
                        "slack_user_id": None,
                        "text": "Request queued.",
                    }
                ],
            },
            channel_id="DREQ",
            thread_ts=None,
        )
        self.assertIsNotNone(result)
        self.assertEqual(service.prepare_calls, ["abc123def456"])
        self.assertEqual([message["channel"] for message in runner.api.messages], ["DREQ", "UOWNER"])
        self.assertEqual(runner.api.messages[0]["text"], "Request queued.")
        self.assertEqual(runner.api.messages[1]["text"], "Owner approval text")

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
                    "request_timeout_seconds": 180,
                    "slack_bot_token_env": "BOT_TOKEN_ENV",
                    "slack_app_token_env": "APP_TOKEN_ENV",
                }
            )
        self.assertEqual(config.slack_bot_token, "xoxb-test")
        self.assertEqual(config.slack_app_token, "xapp-test")


if __name__ == "__main__":
    unittest.main()
