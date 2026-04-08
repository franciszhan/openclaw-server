from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from .config import CoordinatorConfig
from .models import ParsedRequest
from .parser import (
    normalize_request_text,
    parse_public_request,
)


@dataclass(frozen=True)
class ExtractedIntent:
    entity_name: str | None
    entity_company: str | None


class OpenAIIntentExtractor:
    def __init__(self, config: CoordinatorConfig) -> None:
        self.config = config

    def extract(
        self,
        *,
        text: str,
        requester_slack_user_id: str,
        coordinator_slack_user_id: str | None,
        owner_aliases: dict[str, str] | None = None,
        allow_requester_as_owner: bool = False,
    ) -> ParsedRequest:
        fallback = parse_public_request(
            text=text,
            requester_slack_user_id=requester_slack_user_id,
            coordinator_slack_user_id=coordinator_slack_user_id,
            owner_aliases=owner_aliases,
            allow_requester_as_owner=allow_requester_as_owner,
        )
        api_key = os.getenv(self.config.intent_extractor_api_key_env)
        if not api_key:
            return fallback
        try:
            intent = self._extract_intent(
                text=text,
                requester_slack_user_id=requester_slack_user_id,
                coordinator_slack_user_id=coordinator_slack_user_id,
                owner_aliases=owner_aliases,
                allow_requester_as_owner=allow_requester_as_owner,
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
        owner_aliases: dict[str, str] | None,
        allow_requester_as_owner: bool,
        api_key: str,
    ) -> ExtractedIntent:
        normalized = normalize_request_text(text)
        schema = {
            "name": "shared_email_lookup_intent",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entity_name": {"type": "string"},
                    "entity_company": {"type": ["string", "null"]},
                },
                "required": [
                    "entity_name",
                    "entity_company",
                ],
            },
            "strict": True,
        }
        instructions = (
            "Extract intent for a strictly read-only shared email question. "
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
        )
