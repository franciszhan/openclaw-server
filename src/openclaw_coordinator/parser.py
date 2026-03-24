from __future__ import annotations

import re

from .models import ParsedRequest


MENTION_PATTERN = re.compile(r"<@([A-Z0-9]+)>")
LOOKUP_KEYWORDS = (
    "lookup",
    "look up",
    "find",
    "search",
    "emails",
    "email",
    "introduce",
    "intro",
    "summarize",
    "summary",
    "latest info",
    "latest information",
    "latest context",
    "latest update",
    "recent context",
    "recent update",
)
BLOCKED_SCOPE_PHRASES = (
    "all emails",
    "every email",
    "entire inbox",
    "full mailbox",
    "entire mailbox",
    "full inbox",
    "everything in",
)
BLOCKED_EXPORT_PHRASES = (
    "forward me",
    "forward the email",
    "send me the email",
    "show me the raw email",
    "paste the email",
    "verbatim",
    "full thread",
    "entire thread",
    "attachment",
    "download",
    "export",
    "cc me",
)
BLOCKED_SENSITIVE_PHRASES = (
    "personal email",
    "private email",
    "salary",
    "payroll",
    "social security",
    "ssn",
    "bank account",
    "wire instructions",
    "tax return",
    "medical",
    "health insurance",
    "passport",
)


def parse_public_request(
    *,
    text: str,
    requester_slack_user_id: str,
    coordinator_slack_user_id: str | None = None,
    default_owner_slack_user_id: str | None = None,
    owner_aliases: dict[str, str] | None = None,
) -> ParsedRequest:
    owner_slack_user_id = extract_owner_slack_user_id(
        text=text,
        requester_slack_user_id=requester_slack_user_id,
        coordinator_slack_user_id=coordinator_slack_user_id,
        default_owner_slack_user_id=default_owner_slack_user_id,
        owner_aliases=owner_aliases,
    )
    normalized = normalize_request_text(text)
    validate_lookup_request(normalized.lower())

    mode = "read_only"
    entity_name = _extract_entity_name(normalized)
    entity_company = _extract_entity_company(normalized)
    return ParsedRequest(
        owner_slack_user_id=owner_slack_user_id,
        action_type="email_intro_lookup",
        mode=mode,
        entity_name=entity_name,
        entity_company=entity_company,
        purpose=normalized,
        raw_text=text,
    )


def extract_owner_slack_user_id(
    *,
    text: str,
    requester_slack_user_id: str,
    coordinator_slack_user_id: str | None = None,
    default_owner_slack_user_id: str | None = None,
    owner_aliases: dict[str, str] | None = None,
) -> str:
    mentions = MENTION_PATTERN.findall(text)
    candidate_mentions = [
        mention
        for mention in mentions
        if mention != requester_slack_user_id and mention != coordinator_slack_user_id
    ]
    if candidate_mentions:
        return candidate_mentions[-1]
    alias_match = _match_owner_alias(text, owner_aliases or {})
    if alias_match:
        return alias_match
    if default_owner_slack_user_id:
        return default_owner_slack_user_id
    raise ValueError("request must mention a target owner")


def normalize_request_text(text: str) -> str:
    return " ".join(text.strip().split())


def validate_lookup_request(lowered: str) -> None:
    if not any(keyword in lowered for keyword in LOOKUP_KEYWORDS):
        raise ValueError("request must ask for a shared email lookup")
    if any(phrase in lowered for phrase in BLOCKED_SCOPE_PHRASES):
        raise ValueError("request is broader than the allowed shared email lookup scope")
    if any(phrase in lowered for phrase in BLOCKED_EXPORT_PHRASES):
        raise ValueError("request asks for raw email content or forwarding, which is not allowed")
    if any(phrase in lowered for phrase in BLOCKED_SENSITIVE_PHRASES):
        raise ValueError("request appears to target sensitive or off-topic email content")


def _extract_entity_name(text: str) -> str:
    patterns = [
        r"introduce me to (?P<entity>.+?)(?: who | that | he | she | they | and summarize| and tell me| from email| in email|\?|$)",
        r"(?:look up|lookup|find|search|summari[sz]e)\s+(?:emails?\s+)?(?:related to|about|for)\s+(?P<entity>.+?)(?: and summarize| and tell me| from email| in email|\?|$)",
        r"(?:emails?\s+for|emails?\s+about|emails?\s+related to)\s+(?P<entity>.+?)(?: and summarize| and tell me|\?|$)",
        r"(?:latest|recent)\s+(?:info(?:rmation)?|context|update)\s+(?:on|about|for)\s+(?P<entity>.+?)(?: from email| in email| and summarize| and tell me|\?|$)",
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
