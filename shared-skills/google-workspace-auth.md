# Google Workspace Auth

Use this skill when the user wants Gmail or Google Calendar access inside OpenClaw.

## Goal

Help the user connect their own Google account with read-only Gmail and Calendar scopes.

## Commands

- Check status with `google-auth-status`
- Start the consent flow with `connect-google`
- Finish the flow with `finish-google "<callback_url>"`

## Operating Rules

- Do not print client secrets or token file contents.
- Treat the callback URL as sensitive user auth material.
- The shared OAuth client is preinstalled on the VM.
- The per-user token is stored only inside that user VM after they complete auth.

## Default Flow

1. Run `google-auth-status`.
2. If `connected` is `false`, run `connect-google`.
3. Tell the user to open the returned URL in a browser and approve access.
4. Tell the user to paste the full localhost callback URL back into chat.
5. Run `finish-google "<callback_url>"`.
6. Run `google-auth-status` again to confirm the scopes are present.

## Expected Scopes

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/calendar.readonly`

## Response Style

- Be direct.
- Tell the user exactly which step they are on.
- After success, confirm that Google auth is connected and that future Gmail/Calendar requests should work.
