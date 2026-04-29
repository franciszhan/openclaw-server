from __future__ import annotations

import re

from .models import ParsedRequest


MENTION_PATTERN = re.compile(r"<@([A-Z0-9]+)>")


def parse_slack_request(
    *,
    text: str,
    requester_slack_user_id: str,
    coordinator_slack_user_id: str | None = None,
    owner_aliases: dict[str, str] | None = None,
    allow_requester_as_owner: bool = False,
) -> ParsedRequest:
    owner_slack_user_id = extract_owner_slack_user_id(
        text=text,
        requester_slack_user_id=requester_slack_user_id,
        coordinator_slack_user_id=coordinator_slack_user_id,
        owner_aliases=owner_aliases,
        allow_requester_as_owner=allow_requester_as_owner,
    )
    normalized = normalize_request_text(text)
    entity_name = _extract_entity_name(normalized)
    entity_company = _extract_entity_company(normalized)
    return ParsedRequest(
        owner_slack_user_id=owner_slack_user_id,
        action_type="email_intro_lookup",
        mode="read_only",
        entity_name=entity_name,
        entity_company=entity_company,
        purpose=normalized,
        raw_text=text,
    )


def parse_public_request(
    *,
    text: str,
    requester_slack_user_id: str,
    coordinator_slack_user_id: str | None = None,
    owner_aliases: dict[str, str] | None = None,
    allow_requester_as_owner: bool = False,
) -> ParsedRequest:
    return parse_slack_request(
        text=text,
        requester_slack_user_id=requester_slack_user_id,
        coordinator_slack_user_id=coordinator_slack_user_id,
        owner_aliases=owner_aliases,
        allow_requester_as_owner=allow_requester_as_owner,
    )


def extract_owner_slack_user_id(
    *,
    text: str,
    requester_slack_user_id: str,
    coordinator_slack_user_id: str | None = None,
    owner_aliases: dict[str, str] | None = None,
    allow_requester_as_owner: bool = False,
) -> str:
    mentions = MENTION_PATTERN.findall(text)
    candidate_mentions = []
    for mention in mentions:
        if mention == coordinator_slack_user_id:
            continue
        candidate_mentions.append(mention)
    if candidate_mentions:
        return candidate_mentions[-1]
    alias_match = _match_owner_alias(text, owner_aliases or {})
    if alias_match:
        return alias_match
    raise ValueError("request must mention a target owner")


def normalize_request_text(text: str) -> str:
    return " ".join(text.strip().split())


def validate_lookup_request(lowered: str, *, entity_name: str | None = None) -> None:
    # Compatibility shim: request content is no longer rejected before owner approval.
    return None


def _extract_entity_name(text: str) -> str:
    patterns = [
        r"(?:look up|lookup|find|search|summari[sz]e)\s+(?:emails?\s+)?(?:related to|about|for)\s+(?P<entity>.+?)(?: for me| and summarize| and tell me| from email| in email|\?|$)",
        r"(?:latest|recent)\s+(?:info(?:rmation)?|context|updates?)\s+(?:on|about|for)\s+(?P<entity>.+?)(?: from email| in email| and summarize| and tell me|\?|$)",
        r"(?:emails?\s+for|emails?\s+about|emails?\s+related to)\s+(?:the\s+)?(?:(?:latest|recent)\s+(?:info(?:rmation)?|context|updates?)\s+(?:on|about|for)\s+)?(?P<entity>.+?)(?: for me| and summarize| and tell me|\?|$)",
        r"(?:what(?:'s| is)?|whats)\s+(?:the\s+)?latest\s+(?:on|about|for)\s+(?P<entity>.+?)(?: from email| in email| and summarize| and tell me|\?|$)",
        r"(?:what(?:'s| is)?|whats)\s+new\s+(?:on|about|for)\s+(?P<entity>.+?)(?: from email| in email| and summarize| and tell me|\?|$)",
        r"(?:did\s+(?:we|anyone)|have\s+we|has\s+anyone)\s+(?:pass on|passed on|decline|declined|flag|flagged|discuss|review|mention)\s+(?P<entity>.+?)(?:\?|$)",
        r"(?:did\s+.+?\s+)(?:pass on|passed on|decline|declined|flag|flagged|discuss|review|mention)\s+(?P<entity>.+?)(?:\?|$)",
        r"(?:who|when|why|whether|what(?:'s| is)?|whats)\s+.+?\s+(?:on|about|for)\s+(?P<entity>.+?)(?:\?|$)",
        r"(?:who)\s+(?:passed on|declined|flagged|reviewed|mentioned)\s+(?P<entity>.+?)(?:\?|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        entity = match.group("entity").strip(" .?")
        if entity:
            return entity
    return "unspecified topic"


def _extract_entity_company(text: str) -> str | None:
    match = re.search(r"\b(?:at|from)\s+([A-Z][A-Za-z0-9&.\-]+)", text)
    if not match:
        return None
    return match.group(1)


def _match_owner_alias(text: str, owner_aliases: dict[str, str]) -> str | None:
    normalized = normalize_request_text(text).lower()
    for alias, slack_user_id in sorted(
        owner_aliases.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        cleaned_alias = normalize_request_text(alias).strip().lstrip("@").lower()
        if not cleaned_alias:
            continue
        pattern = rf"(?<!\w)@?{re.escape(cleaned_alias)}(?!\w)"
        if re.search(pattern, normalized):
            return slack_user_id
    return None
