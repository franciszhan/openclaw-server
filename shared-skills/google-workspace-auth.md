# Google Workspace Auth

Use this skill when the user wants Gmail or Google Calendar access inside OpenClaw.

## Goal

Help the user connect their own Google account with read-only Gmail and Calendar scopes.

Treat the helper commands in this skill as real tools that already exist on the VM. Do not act like Google auth is unavailable when these commands are present.

## Commands

- Check status with `google-auth-status`
- Start the consent flow with `connect-google`
- Finish the flow with `finish-google "<callback_url>"`

## Operating Rules

- Do not print client secrets or token file contents.
- Treat the callback URL as sensitive user auth material.
- The shared OAuth client is preinstalled on the VM.
- The per-user token is stored only inside that user VM after they complete auth.
- If the user asks to connect Google, Gmail, Calendar, email, inbox, meetings, or schedule access, prefer using these commands over explaining abstract setup steps.
- If the user explicitly asks you to run one of these commands, execute it instead of restating the flow.
- Do not ask the user to SSH or manually inspect files when the helper commands can answer the question.

## Default Flow

1. Run `google-auth-status`.
2. If `connected` is `false`, run `connect-google`.
3. Tell the user to open the returned URL in a browser and approve access.
4. Tell the user to paste the full localhost callback URL back into chat.
5. Run `finish-google "<callback_url>"`.
6. Run `google-auth-status` again to confirm the scopes are present.

## Trigger Phrases

Use this flow when the user says things like:

- `connect google`
- `connect gmail`
- `set up email access`
- `set up calendar access`
- `can you check my inbox`
- `can you look at my calendar`
- `gmail isn't connected`
- `google auth isn't working`

Also use it when the user asks for Gmail or Calendar help and there is any sign auth may not be completed yet.

## Execution Defaults

- Start by running `google-auth-status` unless the user explicitly asked you to run `connect-google` immediately.
- If status says Google is not connected, run `connect-google` and return the consent URL clearly.
- When the user sends back the callback URL, run `finish-google "<callback_url>"` and then immediately run `google-auth-status` again.
- If Google is already connected, say so briefly and continue with the Gmail or Calendar task instead of restarting auth.
- If command output is the important thing, return the relevant output directly instead of long explanation.

## Expected Scopes

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/calendar.readonly`

## Response Style

- Be direct.
- Tell the user exactly which step they are on.
- After success, confirm that Google auth is connected and that future Gmail/Calendar requests should work.
- When returning the consent URL, keep the response short and action-oriented.
