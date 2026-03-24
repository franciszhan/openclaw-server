from __future__ import annotations

from .models import DirectoryEntry, ParsedRequest


def evaluate_request_policy(
    *,
    requester: DirectoryEntry | None,
    owner: DirectoryEntry | None,
    parsed_request: ParsedRequest,
    allow_self_requests: bool = False,
) -> str | None:
    if requester is None:
        return "requester is not registered in the coordinator directory"
    if owner is None:
        return "owner is not registered in the coordinator directory"
    if requester.slack_user_id == owner.slack_user_id and not allow_self_requests:
        return "cross-agent requests must target another opted-in owner"
    if not owner.opt_in:
        return "target owner has not opted into shared access"
    required_capability = (
        "draft_intro_from_email_context"
        if parsed_request.mode == "draft_intro"
        else "email_intro_lookup"
    )
    if required_capability not in owner.shared_capabilities:
        return f"target owner does not allow {required_capability}"
    return None
