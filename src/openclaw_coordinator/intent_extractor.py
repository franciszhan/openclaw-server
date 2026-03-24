from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from .config import CoordinatorConfig
from .models import ParsedRequest
from .parser import (
    extract_owner_slack_user_id,
    normalize_request_text,
    parse_public_request,
    validate_lookup_request,
)


@dataclass(frozen=True)
class ExtractedIntent:
    entity_name: str | None
    entity_company: str | None
    wants_raw_email: bool
    wants_forwarding: bool
    sensitive_topic: bool
    broad_mailbox_request: bool


class OpenAIIntentExtractor:
    def __init__(self, config: CoordinatorConfig) -> None:
        self.config = config

    def extract(
        self,
        *,
        text: str,
        requester_slack_user_id: str,
        coordinator_slack_user_id: str | None,
        default_owner_slack_user_id: str | None,
        owner_aliases: dict[str, str] | None = None,
    ) -> ParsedRequest:
        fallback = parse_public_request(
            text=text,
            requester_slack_user_id=requester_slack_user_id,
            coordinator_slack_user_id=coordinator_slack_user_id,
            default_owner_slack_user_id=default_owner_slack_user_id,
            owner_aliases=owner_aliases,
        )
        api_key = os.getenv(self.config.intent_extractor_api_key_env)
        if not api_key:
            return fallback
        try:
            intent = self._extract_intent(
                text=text,
                requester_slack_user_id=requester_slack_user_id,
                coordinator_slack_user_id=coordinator_slack_user_id,
                default_owner_slack_user_id=default_owner_slack_user_id,
                owner_aliases=owner_aliases,
                api_key=api_key,
            )
        except Exception:
            return fallback
        entity_name = (intent.entity_name or fallback.entity_name).strip()
        entity_company = (intent.entity_company or fallback.entity_company or None)
        return ParsedRequest(
            owner_slack_user_id=fallback.owner_slack_user_id,
            action_type=fallback.action_type,
            mode=fallback.mode,
            entity_name=entity_name or fallback.entity_name,
            entity_company=entity_company,
            purpose=fallback.purpose,
            raw_text=fallback.raw_text,
        )

    def _extract_intent(
        self,
        *,
        text: str,
        requester_slack_user_id: str,
        coordinator_slack_user_id: str | None,
        default_owner_slack_user_id: str | None,
        owner_aliases: dict[str, str] | None,
        api_key: str,
    ) -> ExtractedIntent:
        normalized = normalize_request_text(text)
        lowered = normalized.lower()
        validate_lookup_request(lowered)
        owner_slack_user_id = extract_owner_slack_user_id(
            text=normalized,
            requester_slack_user_id=requester_slack_user_id,
            coordinator_slack_user_id=coordinator_slack_user_id,
            default_owner_slack_user_id=default_owner_slack_user_id,
            owner_aliases=owner_aliases,
        )
        schema = {
            "name": "shared_email_lookup_intent",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "owner_slack_user_id": {"type": "string"},
                    "entity_name": {"type": "string"},
                    "entity_company": {"type": ["string", "null"]},
                    "wants_raw_email": {"type": "boolean"},
                    "wants_forwarding": {"type": "boolean"},
                    "sensitive_topic": {"type": "boolean"},
                    "broad_mailbox_request": {"type": "boolean"},
                },
                "required": [
                    "owner_slack_user_id",
                    "entity_name",
                    "entity_company",
                    "wants_raw_email",
                    "wants_forwarding",
                    "sensitive_topic",
                    "broad_mailbox_request",
                ],
            },
            "strict": True,
        }
        instructions = (
            "Extract intent for a strictly read-only shared email lookup. "
            "Do not expand the user's scope. "
            "Return the mentioned or default owner slack id exactly as given. "
            "Set broad_mailbox_request true for requests asking for all/every/full inbox style access. "
            "Set wants_raw_email true for raw/verbatim/full-thread asks. "
            "Set wants_forwarding true for forward/send/share style asks. "
            "Set sensitive_topic true for personal, payroll, salary, tax, bank, medical, passport, or similar sensitive asks. "
            "entity_name should be the main company, founder, person, or topic the user wants looked up. "
            "entity_company should be null unless a clear company name is explicitly present."
        )
        payload = {
            "model": self.config.intent_extractor_model,
            "messages": [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "text": normalized,
                            "owner_slack_user_id": owner_slack_user_id,
                        }
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": schema,
            },
        }
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.intent_extractor_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = json.loads(content)
        return ExtractedIntent(
            entity_name=str(parsed.get("entity_name") or "").strip() or None,
            entity_company=(
                str(parsed["entity_company"]).strip()
                if parsed.get("entity_company")
                else None
            ),
            wants_raw_email=bool(parsed.get("wants_raw_email", False)),
            wants_forwarding=bool(parsed.get("wants_forwarding", False)),
            sensitive_topic=bool(parsed.get("sensitive_topic", False)),
            broad_mailbox_request=bool(parsed.get("broad_mailbox_request", False)),
        )
