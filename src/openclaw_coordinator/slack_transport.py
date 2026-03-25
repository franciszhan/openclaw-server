from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

from .config import CoordinatorConfig
from .models import RequestRecord
from .service import CoordinatorService

LOGGER = logging.getLogger(__name__)
DM_COMMAND_PATTERN = re.compile(
    r"^\s*(approve|reject|publish|cancel)\s+([a-f0-9]{12})\s*$",
    re.IGNORECASE,
)


class SlackApiError(RuntimeError):
    pass


class SlackApiClient:
    def __init__(self, *, bot_token: str, app_token: str) -> None:
        self.bot_token = bot_token
        self.app_token = app_token

    def open_socket_url(self) -> str:
        response = self._api_call("apps.connections.open", token=self.app_token)
        url = response.get("url")
        if not isinstance(url, str) or not url:
            raise SlackApiError("apps.connections.open returned no websocket url")
        return url

    def post_message(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if blocks:
            payload["blocks"] = blocks
        return self._api_call("chat.postMessage", payload=payload, token=self.bot_token)

    def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        return self._api_call("chat.update", payload=payload, token=self.bot_token)

    def _api_call(
        self,
        method: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str,
    ) -> dict[str, Any]:
        response = requests.post(
            f"https://slack.com/api/{method}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=payload or {},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackApiError(f"{method} failed: {data.get('error', 'unknown_error')}")
        return dict(data)


class SlackSocketModeRunner:
    def __init__(self, config: CoordinatorConfig, service: CoordinatorService) -> None:
        if not config.slack_bot_token or not config.slack_app_token:
            raise ValueError("coordinator config must include slack_bot_token and slack_app_token")
        self.config = config
        self.service = service
        self.api = SlackApiClient(
            bot_token=config.slack_bot_token,
            app_token=config.slack_app_token,
        )

    def run_forever(self) -> None:
        websocket = _load_websocket_module()
        while True:
            try:
                socket_url = self.api.open_socket_url()
                LOGGER.info("opened Slack Socket Mode session")
                connection = websocket.create_connection(socket_url, timeout=30)
                try:
                    while True:
                        raw_message = connection.recv()
                        if raw_message is None:
                            raise RuntimeError("slack socket closed without a final frame")
                        self._handle_envelope(connection, json.loads(raw_message))
                finally:
                    try:
                        connection.close()
                    except Exception:
                        LOGGER.debug("failed to close Slack websocket cleanly", exc_info=True)
            except KeyboardInterrupt:
                raise
            except Exception:
                LOGGER.exception("slack coordinator loop failed; reconnecting")
                time.sleep(5)

    def _handle_envelope(self, connection: Any, envelope: dict[str, Any]) -> None:
        envelope_id = envelope.get("envelope_id")
        if isinstance(envelope_id, str):
            connection.send(json.dumps({"envelope_id": envelope_id}))

        envelope_type = envelope.get("type")
        payload = envelope.get("payload")
        if envelope_type == "events_api" and isinstance(payload, dict):
            self._handle_events_api(payload)
        elif envelope_type == "interactive" and isinstance(payload, dict):
            self._handle_interactive(payload)

    def _handle_events_api(self, payload: dict[str, Any]) -> None:
        event = payload.get("event")
        if not isinstance(event, dict):
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        event_type = str(event.get("type", ""))
        if event_type == "app_mention":
            channel_id = str(event.get("channel") or "")
            if not self._is_allowed_public_channel(channel_id):
                self.api.post_message(
                    channel_id,
                    self._public_channel_denied_text(),
                    thread_ts=str(event.get("thread_ts") or event.get("ts") or ""),
                )
                return
            coordinator_event = {
                "event_id": payload.get("event_id") or event.get("client_msg_id") or event.get("ts"),
                "requester_slack_user_id": event.get("user"),
                "channel_id": channel_id,
                "thread_ts": event.get("thread_ts") or event.get("ts"),
                "text": event.get("text", ""),
                "entrypoint": "public_thread",
            }
        elif event_type == "message" and event.get("channel_type") == "im":
            command = parse_dm_command(str(event.get("text", "")))
            if command is not None:
                self._handle_dm_command(
                    requester_slack_user_id=str(event.get("user") or ""),
                    channel_id=str(event.get("channel") or ""),
                    thread_ts=str(event.get("thread_ts") or event.get("ts") or ""),
                    command=command,
                )
                return
            self.api.post_message(
                str(event.get("channel") or ""),
                "New shared lookup requests must start in the rollout channel. DMs are only used for approvals and review.",
                thread_ts=str(event.get("thread_ts") or event.get("ts") or ""),
            )
            return
        else:
            return
        LOGGER.info(
            "received Slack %s from %s in %s",
            event_type,
            coordinator_event["requester_slack_user_id"],
            coordinator_event["channel_id"],
        )
        self._run_coordinator_action(
            lambda: self.service.submit_slack_request(coordinator_event),
            channel_id=str(coordinator_event["channel_id"]),
            thread_ts=str(coordinator_event["thread_ts"]),
        )

    def _handle_dm_command(
        self,
        *,
        requester_slack_user_id: str,
        channel_id: str,
        thread_ts: str,
        command: tuple[str, str],
    ) -> None:
        action, request_id = command
        LOGGER.info("received DM command %s for %s from %s", action, request_id, requester_slack_user_id)
        if action in {"approve", "reject"}:
            self._run_coordinator_action(
                lambda: self.service.record_owner_decision(
                    request_id,
                    owner_slack_user_id=requester_slack_user_id,
                    decision=action,
                ),
                channel_id=channel_id,
                thread_ts=thread_ts or None,
            )
            return
        if action in {"publish", "cancel"}:
            self._run_coordinator_action(
                lambda: self.service.record_owner_review(
                    request_id,
                    owner_slack_user_id=requester_slack_user_id,
                    decision=action,
                ),
                channel_id=channel_id,
                thread_ts=thread_ts or None,
            )
            return
    def _handle_interactive(self, payload: dict[str, Any]) -> None:
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions:
            return
        action = actions[0]
        if not isinstance(action, dict):
            return
        action_id = str(action.get("action_id", ""))
        action_value = _parse_action_value(action.get("value"))
        user = payload.get("user") or {}
        channel = payload.get("channel") or {}
        container = payload.get("container") or {}
        channel_id = str(channel.get("id") or container.get("channel_id") or "")
        thread_ts = str(container.get("thread_ts") or container.get("message_ts") or "")
        message_ts = str(container.get("message_ts") or "")
        user_id = str(user.get("id") or "")

        if action_id == "owner_decision":
            LOGGER.info("received owner decision action from %s", user_id)
            self._ack_interactive_decision(
                channel_id=channel_id,
                message_ts=message_ts,
                request_id=str(action_value.get("request_id") or ""),
                text="Approval received. Running the scoped lookup now.",
            )
            result = self._run_coordinator_action(
                lambda: self.service.record_owner_decision(
                    str(action_value["request_id"]),
                    owner_slack_user_id=user_id,
                    decision=str(action_value["decision"]),
                ),
                channel_id=channel_id,
                thread_ts=thread_ts or None,
            )
            self._refresh_interactive_message(channel_id, message_ts, action_value, result)
            return
        if action_id in {"owner_decision_approve", "owner_decision_reject"}:
            decision = "approve" if action_id.endswith("approve") else "reject"
            LOGGER.info("received owner decision action %s from %s", decision, user_id)
            ack_text = (
                "Approval received. Running the scoped lookup now."
                if decision == "approve"
                else "Rejection received. Closing this request."
            )
            self._ack_interactive_decision(
                channel_id=channel_id,
                message_ts=message_ts,
                request_id=str(action_value.get("request_id") or ""),
                text=ack_text,
            )
            result = self._run_coordinator_action(
                lambda: self.service.record_owner_decision(
                    str(action_value["request_id"]),
                    owner_slack_user_id=user_id,
                    decision=decision,
                ),
                channel_id=channel_id,
                thread_ts=thread_ts or None,
            )
            self._refresh_interactive_message(channel_id, message_ts, action_value, result)
            return
        if action_id == "review_decision":
            LOGGER.info("received owner review action from %s", user_id)
            self._ack_interactive_decision(
                channel_id=channel_id,
                message_ts=message_ts,
                request_id=str(action_value.get("request_id") or ""),
                text="Review decision received. Updating request state.",
            )
            result = self._run_coordinator_action(
                lambda: self.service.record_owner_review(
                    str(action_value["request_id"]),
                    owner_slack_user_id=user_id,
                    decision=str(action_value["decision"]),
                ),
                channel_id=channel_id,
                thread_ts=thread_ts or None,
            )
            self._refresh_interactive_message(channel_id, message_ts, action_value, result)
            return
        if action_id in {"review_decision_publish", "review_decision_cancel"}:
            decision = "publish" if action_id.endswith("publish") else "cancel"
            LOGGER.info("received owner review action %s from %s", decision, user_id)
            ack_text = (
                "Publish received. Finalizing the public reply."
                if decision == "publish"
                else "Cancel received. Closing this request."
            )
            self._ack_interactive_decision(
                channel_id=channel_id,
                message_ts=message_ts,
                request_id=str(action_value.get("request_id") or ""),
                text=ack_text,
            )
            result = self._run_coordinator_action(
                lambda: self.service.record_owner_review(
                    str(action_value["request_id"]),
                    owner_slack_user_id=user_id,
                    decision=decision,
                ),
                channel_id=channel_id,
                thread_ts=thread_ts or None,
            )
            self._refresh_interactive_message(channel_id, message_ts, action_value, result)
            return
    def _run_coordinator_action(
        self,
        operation: callable[[], dict[str, object]],
        *,
        channel_id: str,
        thread_ts: str | None,
    ) -> dict[str, object] | None:
        try:
            result = operation()
        except ValueError as error:
            self.api.post_message(
                channel_id,
                f"Request could not be processed: {error}",
                thread_ts=thread_ts,
            )
            return None
        except Exception:
            LOGGER.exception("coordinator action failed")
            self.api.post_message(
                channel_id,
                "The coordinator hit an unexpected error while processing that request.",
                thread_ts=thread_ts,
            )
            return None
        request = result.get("request")
        if isinstance(request, dict):
            LOGGER.info(
                "coordinator request %s now %s",
                request.get("request_id"),
                request.get("status"),
            )
        self._post_actions(result)
        return result

    def _refresh_interactive_message(
        self,
        channel_id: str,
        message_ts: str,
        action_value: dict[str, Any],
        result: dict[str, object] | None,
    ) -> None:
        if not channel_id or not message_ts:
            return
        request: dict[str, Any] | None = None
        if result and isinstance(result.get("request"), dict):
            request = dict(result["request"])
        else:
            request_id = action_value.get("request_id")
            if isinstance(request_id, str):
                try:
                    request = self.service.store.load_request(request_id).to_dict()
                except Exception:
                    LOGGER.debug("failed to reload request %s for message refresh", request_id, exc_info=True)
        if not request:
            return
        text, blocks = self._interactive_resolution_message(request)
        try:
            self.api.update_message(channel_id, message_ts, text, blocks=blocks)
        except Exception:
            LOGGER.exception("failed to refresh interactive Slack message for %s", request.get("request_id"))

    def _ack_interactive_decision(
        self,
        *,
        channel_id: str,
        message_ts: str,
        request_id: str,
        text: str,
    ) -> None:
        if not channel_id or not message_ts or not request_id:
            return
        try:
            self.api.update_message(
                channel_id,
                message_ts,
                text,
                blocks=[_section_block(f"*Request `{request_id}`*\n{text}")],
            )
        except Exception:
            LOGGER.exception("failed to acknowledge interactive Slack decision for %s", request_id)

    def _post_actions(self, result: dict[str, object]) -> None:
        request = result.get("request")
        actions = result.get("actions")
        if not isinstance(request, dict) or not isinstance(actions, list):
            return
        for action in actions:
            if not isinstance(action, dict):
                continue
            self._post_action(request, action)

    def _post_action(self, request: dict[str, Any], action: dict[str, Any]) -> None:
        kind = str(action["kind"])
        text = str(action["text"])
        if kind in {
            "public_ack",
            "public_rejected",
            "public_failed",
            "public_cancelled",
            "public_published",
        }:
            self.api.post_message(
                str(action["channel_id"]),
                text,
                thread_ts=str(action["thread_ts"]) if action.get("thread_ts") else None,
                blocks=self._public_blocks(request, kind, text),
            )
            return
        if kind == "owner_dm_approval":
            self.api.post_message(
                str(action["slack_user_id"]),
                text,
                blocks=self._owner_approval_blocks(request, text),
            )
            return
        if kind == "owner_dm_review":
            self.api.post_message(
                str(action["slack_user_id"]),
                text,
                blocks=self._owner_review_blocks(request, text),
            )

    def _interactive_resolution_message(
        self,
        request: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        record = RequestRecord.from_dict(request)
        status = record.status
        if status == "executing":
            text = f"Request `{record.request_id}` approved. Running the scoped lookup now."
        elif status == "owner_review_pending":
            text = f"Request `{record.request_id}` approved. Review the result below."
        elif status == "owner_rejected":
            text = f"Request `{record.request_id}` rejected."
        elif status == "published":
            text = f"Request `{record.request_id}` published."
        elif status == "failed":
            user_error = ""
            if isinstance(record.result_metadata, dict):
                user_error = str(record.result_metadata.get("user_error") or "").strip()
            failure_reason = user_error or (record.failure_reason or "unknown error").strip()
            text = f"Request `{record.request_id}` ended in `{status}`: {failure_reason}"
        else:
            text = f"Request `{record.request_id}` is now `{status}`."
        return text, [_section_block(text)]

    def _public_blocks(
        self,
        request: dict[str, Any],
        action_kind: str,
        text: str,
    ) -> list[dict[str, Any]] | None:
        return None

    def _is_allowed_public_channel(self, channel_id: str) -> bool:
        allowed = set(self.config.allowed_public_channel_ids)
        if not allowed:
            return False
        return channel_id in allowed

    def _public_channel_denied_text(self) -> str:
        allowed = self.config.allowed_public_channel_ids
        if len(allowed) == 1:
            return f"AgentCoordinator only accepts new requests in <#{allowed[0]}>."
        return "AgentCoordinator only accepts new requests in the configured rollout channels."

    def _owner_approval_blocks(
        self,
        request: dict[str, Any],
        text: str,
    ) -> list[dict[str, Any]]:
        entity_name = str(request.get("entity_name") or "unspecified contact")
        purpose = str(request.get("purpose") or "").strip()
        return [
            _section_block(
                "*Shared Email Request*\n"
                f"<@{request['requester_slack_user_id']}> requested "
                "a shared email lookup."
            ),
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Request ID:*\n`{request['request_id']}`"},
                    {"type": "mrkdwn", "text": f"*Entity:*\n{entity_name[:150]}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Requester:*\n<@{request['requester_slack_user_id']}>",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Owner:*\n<@{request['owner_slack_user_id']}>",
                    },
                ],
            },
            _section_block(f"*Purpose:*\n{purpose[:2500]}"),
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "owner_decision_approve",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "value": json.dumps({"request_id": request["request_id"]}),
                    },
                    {
                        "type": "button",
                        "action_id": "owner_decision_reject",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "value": json.dumps({"request_id": request["request_id"]}),
                    },
                ],
            },
        ]

    def _owner_review_blocks(
        self,
        request: dict[str, Any],
        text: str,
    ) -> list[dict[str, Any]]:
        record = RequestRecord.from_dict(request)
        return [
            _section_block(
                f"*Review Result*\nRequest `{request['request_id']}` is ready for owner review."
            ),
            _section_block(format_preview_text(record)),
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "review_decision_publish",
                        "text": {"type": "plain_text", "text": "Publish"},
                        "style": "primary",
                        "value": json.dumps({"request_id": request["request_id"]}),
                    },
                    {
                        "type": "button",
                        "action_id": "review_decision_cancel",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "style": "danger",
                        "value": json.dumps({"request_id": request["request_id"]}),
                    },
                ],
            },
        ]

def format_preview_text(record: RequestRecord) -> str:
    result = record.result or {}
    if record.mode == "draft_intro":
        draft_intro = str(result.get("draft_intro", "")).strip()
        rationale = str(result.get("rationale", "")).strip()
        preview_lines = [f"*Draft*: {draft_intro or '(empty)'}"]
        if rationale:
            preview_lines.append(f"*Rationale*: {rationale}")
        return "\n".join(preview_lines)
    preview_lines = [
        f"*Context summary*: {result.get('summary_details', '')}",
        f"*Business update*: {result.get('business_update', '')}",
        f"*Best point of contact*: {result.get('best_point_of_contact', '')}",
    ]
    references = result.get("references", [])
    if isinstance(references, list) and references:
        reference_lines = []
        for reference in references[:3]:
            if not isinstance(reference, dict):
                continue
            subject = reference.get("subject") or reference.get("message_id") or "message"
            sender = reference.get("sender") or "unknown sender"
            date = reference.get("date") or "unknown date"
            reference_lines.append(f"• {subject} ({sender}, {date})")
        if reference_lines:
            preview_lines.append("*References:*\n" + "\n".join(reference_lines))
    return "\n".join(preview_lines)


def _section_block(text: str) -> dict[str, Any]:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text[:3000]},
    }


def _parse_action_value(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        raise ValueError("interactive Slack action is missing a value payload")
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("interactive Slack action payload must be a JSON object")
    return payload


def parse_dm_command(text: str) -> tuple[str, str] | None:
    match = DM_COMMAND_PATTERN.match(text)
    if not match:
        return None
    return match.group(1).lower(), match.group(2).lower()


def _load_websocket_module() -> Any:
    try:
        import websocket  # type: ignore
    except ImportError as error:
        raise RuntimeError(
            "Slack Socket Mode requires the websocket-client package. "
            "Install it with `python3 -m pip install websocket-client`."
        ) from error
    return websocket
