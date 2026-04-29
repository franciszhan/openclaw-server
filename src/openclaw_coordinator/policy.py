from __future__ import annotations

from .models import DirectoryEntry, ParsedRequest


def evaluate_request_policy(
    *,
    requester: DirectoryEntry | None,
    owner: DirectoryEntry | None,
    parsed_request: ParsedRequest,
    allow_self_requests: bool = False,
) -> str | None:
    if owner is None:
        return "owner is not registered in the coordinator directory"
    return None
