# Employee VM Onboarding

This is the minimum information to collect before provisioning a new employee VM.

## What To Ask For

- Full name
- Short user ID
  Example: `alice`, `sam-lee`
- Work email
- Slack user ID
- Timezone
- Preferred editor or shell, if they care
- Git hosting username
  Example: GitHub username
- Any private repos or org access they need on day one
- Any API-backed tools they need inside OpenClaw
  Example: Linear, GitHub, Slack, Vercel
- Any package or CLI requests beyond the base image
- Any personal dotfiles or setup scripts they want applied
- Whether they need outbound internet access beyond normal LLM/API use
- Whether they want to opt into shared read-only email lookups from teammates

Do not ask them to send secrets in chat or email. Collect only identifiers and access requirements. Secrets should be entered by the employee inside their own VM after first login or delivered through your normal secret-management path.

## Admin Intake Template

Copy this into your internal ticket:

```text
Employee name:
User ID:
Email:
Timezone:
Git username:
Slack user ID:
Required repos/orgs:
Required external tools:
Extra CLI/packages requested:
Dotfiles/setup repo:
Special notes:
Shared access opt-in:
```

## Provisioning Inputs

Create one per-user manifest JSON file like:

```json
{
  "user_id": "alice",
  "slack_user_id": "U12345678",
  "profile": "default",
  "email": "alice@example.com",
  "timezone": "America/Toronto",
  "git_username": "alice-example",
  "shared_access": {
    "opt_in": true,
    "capabilities": [
      "email_intro_lookup"
    ],
    "email_intro_lookup": {
      "allowedRecipientFilters": [
        "leads@tribecap.co",
        "portfolio-passive@tribecap.co",
        "portfolio-active@tribecap.co",
        "crypto-passive@tribecap.co",
        "crypto@tribecap.co"
      ],
      "command": [
        "/usr/local/bin/company-email-intro-lookup",
        "--request",
        "{request_path}",
        "--response",
        "{response_path}"
      ]
    }
  },
  "openclaw": {
    "env": {
      "OPENAI_API_KEY": "sk-..."
    },
    "authProfiles": {
      "openai:default": {
        "provider": "openai",
        "type": "api_key",
        "keyEnv": "OPENAI_API_KEY"
      }
    },
    "channels": {
      "slack": {
        "enabled": true,
        "mode": "socket",
        "botToken": "xoxb-...",
        "appToken": "xapp-...",
        "allowFrom": ["U12345678"]
      }
    }
  }
}
```

Then provision:

```bash
sudo openclaw-hostctl provision alice \
  --display-name "Alice Example" \
  --user-config /srv/openclaw/user-configs/alice.json
```

The host stores the full manifest under the VM directory, but the guest-facing `/etc/openclaw/config.json` only gets the non-OpenClaw metadata fields. The `openclaw` section is reserved for the activation step.
The `shared_access` section is also reserved for activation and the coordinator flow.
If you omit the `command` arrays, activation now defaults them to the built-in `/usr/local/bin/company-email-intro-lookup` and `/usr/local/bin/company-email-intro-draft` wrappers, which call `openclaw agent --local` inside the guest.

## Activation Inputs

Reuse the same manifest file for activation.

Activate the user with:

```bash
sudo openclaw-hostctl activate-user alice \
  --manifest /srv/openclaw/user-configs/alice.json \
  --restart
```

This writes directly into the real OpenClaw state under `/home/admin/.openclaw` inside the guest:

- `.env`
- `openclaw.json`
- `agents/main/agent/auth-profiles.json`
- `credentials/slack-default-allowFrom.json`
- `shared-access.json`

It also does the extra setup needed so OpenClaw is usable immediately after boot:

- defaults the agent model to `openai/gpt-5.4` unless you override `openclaw.defaultModel`
- creates a gateway auth token in `openclaw.json`
- installs and enables the user-level `openclaw-gateway.service`
- queues a guest-side gateway reload/restart on the next boot so the new config is live without a manual SSH step
- enables linger for `admin` so the gateway can stay up without an active SSH session
- tightens `/home/admin/.openclaw` and `credentials/` to `0700`
- installs `/usr/local/bin/openclaw-shared-access` for coordinator-only typed shared-access requests
- seeds Google Workspace readonly auth helpers when the host has `/etc/openclaw/google-oauth-client.json`

## Google Workspace First-Run

If the host has the shared Google OAuth client configured, each VM starts with these helper commands:

- `google-auth-status`
- `connect-google`
- `finish-google "<callback_url>"`

Expected employee flow:

1. Ask OpenClaw to connect Google.
2. The agent should run `google-auth-status`.
3. If not connected, it should run `connect-google` and return the consent URL.
4. The employee opens the URL, approves readonly Gmail + Calendar access, and pastes the localhost callback URL back.
5. The agent runs `finish-google "<callback_url>"`.
6. The agent runs `google-auth-status` again to confirm the connection.

This does not copy the shared Google OAuth client secret into each VM. The shared client stays on the host, each VM gets only the helper commands, and each employee still authenticates their own Google account so only their token ends up in their own VM.

## Post-Activation Check

After activation, verify the guest before handing it to the employee:

```bash
ssh -J root@HOST_OR_TAILSCALE_IP admin@GUEST_IP
openclaw status
```

You want all of these:

- Gateway reachable
- Slack channel `OK`
- default model set to OpenAI
- no security warnings about missing gateway auth

If you patch OpenClaw config on an already-running VM instead of using `activate-user --restart`, run:

```bash
openclaw gateway restart
```

That is the fast path to make the gateway pick up the new config without a full VM reboot.

## Employee First Login

Send the employee:

- their VM access path
- how to authenticate
- where to add their own secrets
- what base skills are already installed
- who to contact if they break their setup

For this deployment model, the normal admin access pattern is:

```bash
ssh -J root@HOST_OR_TAILSCALE_IP admin@GUEST_IP
```

If the employee will SSH directly, give them the equivalent host-specific path you want to support.
