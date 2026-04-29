from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from openclaw_coordinator.config import CoordinatorConfig
from openclaw_coordinator.intent_extractor import OpenAIIntentExtractor
from openclaw_coordinator.models import DirectoryEntry
from openclaw_coordinator.parser import parse_slack_request
from openclaw_coordinator.service import CoordinatorService
from openclaw_coordinator.store import CoordinatorStore


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[DirectoryEntry, dict[str, object]]] = []

    def execute(self, owner: DirectoryEntry, request) -> dict[str, object]:
        self.calls.append((owner, request.to_dict()))
        return {
            "answer": "The recent emails suggest the team likely passed on the company, though the evidence is somewhat indirect.",
            "supporting_context": "A partner thread mentions passing, follow-up diligence concerns, and no recent re-engagement.",
            "why_these_emails": "These were the newest threads directly discussing the decision and the reasoning behind it.",
            "references": [
                {
                    "message_id": "msg-1",
                    "subject": "Met founder at conference",
                    "sender": "boris@example.com",
                    "date": "2026-03-10",
                }
            ],
        }


class FailingExecutor:
    def execute(self, owner: DirectoryEntry, request) -> dict[str, object]:
        raise __import__("subprocess").CalledProcessError(
            1,
            ["openclaw-hostctl", "shared-access", "execute", owner.vm_user_id],
            stderr="lookup returned no supporting references",
        )


class FakeIntentExtractor:
    def extract(
        self,
        *,
        text: str,
        requester_slack_user_id: str,
        coordinator_slack_user_id: str | None,
        owner_aliases: dict[str, str] | None = None,
        allow_requester_as_owner: bool = False,
    ):
        return parse_slack_request(
            text=text,
            requester_slack_user_id=requester_slack_user_id,
            coordinator_slack_user_id=coordinator_slack_user_id,
            owner_aliases=owner_aliases,
            allow_requester_as_owner=allow_requester_as_owner,
        )


def example_config(state_root: Path) -> CoordinatorConfig:
    return CoordinatorConfig(
        state_root=state_root,
        relay_command=["openclaw-hostctl", "shared-access", "execute", "{owner_vm_user_id}"],
        coordinator_slack_user_id="UCOORD",
        request_timeout_seconds=30,
        intent_extractor_model="gpt-5-nano",
        intent_extractor_api_key_env="OPENAI_API_KEY",
        intent_extractor_timeout_seconds=10,
        slack_bot_token=None,
        slack_app_token=None,
        allow_self_requests_for_testing=False,
    )


class CoordinatorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temp_dir.name)
        self.store = CoordinatorStore(self.state_root)
        self.executor = FakeExecutor()
        self.service = CoordinatorService(
            example_config(self.state_root),
            self.store,
            self.executor,
            intent_extractor=FakeIntentExtractor(),
        )
        self.service.init_state()
        self.requester = DirectoryEntry(
            slack_user_id="UREQUEST",
            vm_user_id="francis",
            display_name="Francis",
            vm_address="172.31.0.11",
            opt_in=True,
            shared_capabilities=["email_intro_lookup"],
        )
        self.owner = DirectoryEntry(
            slack_user_id="UOWNER",
            vm_user_id="boris",
            display_name="Boris",
            vm_address="172.31.0.12",
            opt_in=True,
            shared_capabilities=["email_intro_lookup"],
        )
        self.service.upsert_directory_entry(self.requester)
        self.service.upsert_directory_entry(self.owner)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_submit_request_creates_pending_owner_approval(self) -> None:
        result = self.service.submit_dm_request(
            {
                "event_id": "evt-1",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails related to this founder he mentioned in email?",
            }
        )
        request = result["request"]
        self.assertEqual(request["status"], "pending_owner_approval")
        self.assertEqual(request["owner_slack_user_id"], "UOWNER")
        self.assertEqual(request["mode"], "read_only")
        self.assertIsNotNone(request["result"])
        self.assertEqual(len(result["actions"]), 2)
        self.assertEqual(result["actions"][0]["kind"], "requester_dm_ack")
        self.assertEqual(result["actions"][0]["channel_id"], "DREQ")
        self.assertEqual(result["actions"][1]["kind"], "owner_dm_approval")
        self.assertIn("*Answer*", result["actions"][1]["text"])
        self.assertEqual(len(self.executor.calls), 1)

    def test_duplicate_event_is_ignored(self) -> None:
        first = self.service.submit_dm_request(
            {
                "event_id": "evt-1",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails related to this founder?",
            }
        )
        second = self.service.submit_dm_request(
            {
                "event_id": "evt-1",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails related to this founder?",
            }
        )
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["request"]["request_id"], second["request"]["request_id"])
        self.assertEqual(len(self.store.list_requests()), 1)

    def test_self_request_is_routed_for_approval(self) -> None:
        self.service = CoordinatorService(
            example_config(self.state_root),
            self.store,
            self.executor,
            intent_extractor=FakeIntentExtractor(),
        )
        result = self.service.submit_dm_request(
            {
                "event_id": "evt-self-1",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.333",
                "text": "<@UCOORD> <@UREQUEST> latest info on 1money",
            }
        )
        request = result["request"]
        self.assertEqual(request["owner_slack_user_id"], "UREQUEST")
        self.assertEqual(request["requester_slack_user_id"], "UREQUEST")
        self.assertEqual(request["status"], "pending_owner_approval")

    def test_non_opted_in_owner_is_routed_for_approval(self) -> None:
        self.service.upsert_directory_entry(
            DirectoryEntry(
                slack_user_id="UDENY",
                vm_user_id="notopted",
                display_name="Nope",
                vm_address="172.31.0.13",
                opt_in=False,
                shared_capabilities=["email_intro_lookup"],
            )
        )
        result = self.service.submit_dm_request(
            {
                "event_id": "evt-2",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UDENY> look up emails about this founder?",
            }
        )
        self.assertEqual(result["request"]["status"], "pending_owner_approval")
        self.assertEqual(result["request"]["owner_slack_user_id"], "UDENY")

    def test_unregistered_requester_is_routed_for_owner_approval(self) -> None:
        result = self.service.submit_dm_request(
            {
                "event_id": "evt-unregistered",
                "requester_slack_user_id": "UNEW",
                "channel_id": "DNEW",
                "thread_ts": "111.444",
                "text": "<@UCOORD> can <@UOWNER> find every email about 1Money?",
            }
        )
        self.assertEqual(result["request"]["status"], "pending_owner_approval")
        self.assertEqual(result["request"]["requester_slack_user_id"], "UNEW")
        self.assertIsNone(result["request"]["requester_vm_user_id"])
        self.assertEqual(result["actions"][0]["kind"], "requester_dm_ack")

    def test_owner_reject_sends_requester_dm_rejection(self) -> None:
        submit = self.service.submit_dm_request(
            {
                "event_id": "evt-3",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails about this founder?",
            }
        )
        request_id = submit["request"]["request_id"]
        result = self.service.record_owner_decision(
            request_id,
            owner_slack_user_id="UOWNER",
            decision="reject",
        )
        self.assertEqual(result["request"]["status"], "owner_rejected")
        self.assertEqual(result["actions"][0]["kind"], "requester_dm_rejected")

    def test_lookup_miss_gets_friendly_failure_message_before_owner_approval(self) -> None:
        service = CoordinatorService(
            example_config(self.state_root),
            self.store,
            FailingExecutor(),
            intent_extractor=FakeIntentExtractor(),
        )
        submit = service.submit_dm_request(
            {
                "event_id": "evt-miss",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails about Rava Money?",
            }
        )
        self.assertEqual(submit["request"]["status"], "failed")
        self.assertIn("did not find enough supporting emails", submit["actions"][0]["text"])
        self.assertEqual(submit["actions"][0]["kind"], "requester_dm_failed")
        self.assertIn("did not find enough supporting emails", submit["request"]["result_metadata"]["user_error"])

    def test_owner_approve_publishes_precomputed_result_to_requester_dm(self) -> None:
        submit = self.service.submit_dm_request(
            {
                "event_id": "evt-4",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails related to this founder he mentioned in email?",
            }
        )
        request_id = submit["request"]["request_id"]
        self.assertEqual(submit["request"]["status"], "pending_owner_approval")
        self.assertIsNotNone(submit["request"]["result"])
        self.assertEqual(len(self.executor.calls), 1)
        approved = self.service.record_owner_decision(
            request_id,
            owner_slack_user_id="UOWNER",
            decision="approve",
        )
        self.assertEqual(approved["request"]["status"], "published")
        self.assertIsNotNone(approved["request"]["published_at"])
        self.assertEqual(len(approved["actions"]), 1)
        self.assertEqual(approved["actions"][0]["kind"], "requester_dm_published")
        self.assertEqual(approved["actions"][0]["channel_id"], "DREQ")
        self.assertIn("*Answer*", approved["actions"][0]["text"])
        self.assertIn("*References*", approved["actions"][0]["text"])
        self.assertEqual(len(self.executor.calls), 1)

    def test_legacy_owner_review_cancel_sends_requester_dm_notice(self) -> None:
        submit = self.service.submit_dm_request(
            {
                "event_id": "evt-4b",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails about this founder?",
            }
        )
        request_id = submit["request"]["request_id"]
        record = self.store.load_request(request_id)
        self.store.save_request(
            replace(
                record,
                status="owner_review_pending",
                result={
                    "answer": "draft",
                    "supporting_context": "context",
                    "why_these_emails": "because",
                    "references": [],
                },
            )
        )
        cancelled = self.service.record_owner_review(
            request_id,
            owner_slack_user_id="UOWNER",
            decision="cancel",
        )
        self.assertEqual(cancelled["request"]["status"], "failed")
        self.assertEqual(cancelled["actions"][0]["kind"], "requester_dm_cancelled")

    def test_audit_log_is_written(self) -> None:
        self.service.submit_dm_request(
            {
                "event_id": "evt-6",
                "requester_slack_user_id": "UREQUEST",
                "channel_id": "DREQ",
                "thread_ts": "111.222",
                "text": "<@UCOORD> can <@UOWNER> look up emails about this founder?",
            }
        )
        audit_lines = self.store.audit_log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertTrue(audit_lines)
        event = json.loads(audit_lines[0])
        self.assertEqual(event["event_type"], "request_submitted")


class ParserSafetyTests(unittest.TestCase):
    def test_lookup_style_requests_from_non_technical_users_are_not_content_filtered(self) -> None:
        cases = [
            {
                "name": "portfolio company summary",
                "text": "<@UCOORD> can <@UOWNER> look up emails related to 1Money and summarize the latest context?",
                "entity": "1Money",
            },
            {
                "name": "crm style ask",
                "text": "<@UCOORD> can <@UOWNER> please find emails about Ramp and summarize anything important from the last few conversations",
                "entity": "Ramp",
            },
            {
                "name": "pass decision ask",
                "text": "<@UCOORD> did <@UOWNER> pass on Databento?",
                "entity": "Databento",
            },
            {
                "name": "compliance concern ask",
                "text": "<@UCOORD> can <@UOWNER> tell me who flagged compliance concerns on Ramp?",
                "entity": "Ramp",
            },
            {
                "name": "broad mailbox ask",
                "text": "<@UCOORD> can <@UOWNER> show me all emails about crypto deals?",
                "entity": "crypto deals",
            },
            {
                "name": "raw email ask",
                "text": "<@UCOORD> can <@UOWNER> paste the email about 1Money here?",
            },
            {
                "name": "forwarding ask",
                "text": "<@UCOORD> can <@UOWNER> forward me the email from the founder at 1Money?",
            },
            {
                "name": "sensitive off topic ask",
                "text": "<@UCOORD> can <@UOWNER> search emails for salary discussions with that founder?",
            },
            {
                "name": "private email ask",
                "text": "<@UCOORD> can <@UOWNER> find Boris's personal email threads about this company?",
            },
            {
                "name": "generic email summary",
                "text": "<@UCOORD> can <@UOWNER> summarize emails about Figure from the last year?",
                "entity": "Figure from the last year",
            },
            {
                "name": "public alias owner and latest info phrasing",
                "text": "<@UCOORD> @Boris can you send me latest info on 1Money",
                "entity": "1Money",
                "owner_aliases": {"Boris": "UOWNER"},
            },
            {
                "name": "public full-name alias owner and latest context phrasing",
                "text": "<@UCOORD> @Boris Revsin can you give me the latest context on Ramp?",
                "entity": "Ramp",
                "owner_aliases": {"Boris Revsin": "UOWNER"},
            },
            {
                "name": "plain latest-on phrasing",
                "text": "<@UCOORD> <@UOWNER> What's the latest on Grass?",
                "entity": "Grass",
            },
            {
                "name": "dm check owner email latest updates phrasing",
                "text": "hey spark, can you check @Francis Zhan email for the latest updates on EDG?",
                "entity": "EDG",
                "owner_aliases": {"Francis Zhan": "UOWNER"},
            },
            {
                "name": "not a lookup",
                "text": "<@UCOORD> can <@UOWNER> help me think about IC prep?",
            },
        ]

        for case in cases:
            with self.subTest(case["name"]):
                parsed = parse_slack_request(
                    text=case["text"],
                    requester_slack_user_id="UREQUEST",
                    coordinator_slack_user_id="UCOORD",
                    owner_aliases=case.get("owner_aliases"),
                )
                self.assertEqual(parsed.mode, "read_only")
                self.assertEqual(parsed.action_type, "email_intro_lookup")
                self.assertEqual(parsed.owner_slack_user_id, "UOWNER")
                if "entity" in case:
                    self.assertEqual(parsed.entity_name, case["entity"])


class OpenAIIntentExtractorTests(unittest.TestCase):
    def test_openai_extractor_uses_model_output_for_entity(self) -> None:
        config = example_config(Path("/tmp/state"))
        extractor = OpenAIIntentExtractor(config)
        response_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "entity_name": "Rava Money",
                                "entity_company": "Rava Money",
                            }
                        )
                    }
                }
            ]
        }

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return response_payload

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            with mock.patch(
                "openclaw_coordinator.intent_extractor.requests.post",
                return_value=FakeResponse(),
            ):
                parsed = extractor.extract(
                    text="<@UCOORD> <@UOWNER> can you look at my emails to find latest information on rava money",
                    requester_slack_user_id="UREQUEST",
                    coordinator_slack_user_id="UCOORD",
                    owner_aliases=None,
                )
        self.assertEqual(parsed.owner_slack_user_id, "UOWNER")
        self.assertEqual(parsed.entity_name, "Rava Money")
        self.assertEqual(parsed.entity_company, "Rava Money")

    def test_openai_extractor_falls_back_without_api_key(self) -> None:
        config = example_config(Path("/tmp/state"))
        extractor = OpenAIIntentExtractor(config)
        with mock.patch.dict(os.environ, {}, clear=True):
            parsed = extractor.extract(
                text="<@UCOORD> can <@UOWNER> look up emails related to Figure and summarize the latest context?",
                requester_slack_user_id="UREQUEST",
                coordinator_slack_user_id="UCOORD",
                owner_aliases=None,
            )
        self.assertEqual(parsed.entity_name, "Figure")

    def test_openai_extractor_ignores_model_flag_fields_and_uses_regex_validation(self) -> None:
        config = example_config(Path("/tmp/state"))
        extractor = OpenAIIntentExtractor(config)
        response_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "entity_name": "1Money",
                                "entity_company": "1Money",
                                "wants_raw_email": True,
                                "wants_forwarding": True,
                                "sensitive_topic": True,
                                "broad_mailbox_request": True,
                            }
                        )
                    }
                }
            ]
        }

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return response_payload

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
            with mock.patch(
                "openclaw_coordinator.intent_extractor.requests.post",
                return_value=FakeResponse(),
            ):
                parsed = extractor.extract(
                    text="<@UCOORD> can <@UOWNER> look up emails about 1Money for me?",
                    requester_slack_user_id="UREQUEST",
                    coordinator_slack_user_id="UCOORD",
                    owner_aliases=None,
                )
        self.assertEqual(parsed.entity_name, "1Money")
        self.assertEqual(parsed.entity_company, "1Money")


if __name__ == "__main__":
    unittest.main()
