# Employee VM Onboarding

This is the minimum information to collect before provisioning a new employee VM.

## What To Ask For

- Full name
- Short user ID
  Example: `alice`, `sam-lee`
- Work email
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

Do not ask them to send secrets in chat or email. Collect only identifiers and access requirements. Secrets should be entered by the employee inside their own VM after first login or delivered through your normal secret-management path.

## Admin Intake Template

Copy this into your internal ticket:

```text
Employee name:
User ID:
Email:
Timezone:
Git username:
Required repos/orgs:
Required external tools:
Extra CLI/packages requested:
Dotfiles/setup repo:
Special notes:
```

## Provisioning Inputs

Create a per-user config JSON file like:

```json
{
  "user_id": "alice",
  "profile": "default",
  "email": "alice@example.com",
  "timezone": "America/Toronto",
  "git_username": "alice-example"
}
```

Then provision:

```bash
sudo openclaw-hostctl provision alice \
  --display-name "Alice Example" \
  --user-config /srv/openclaw/user-configs/alice.json
```

The provisioned copy is also persisted under the VM directory on the host so later activation steps can reuse it without asking you for the same profile again.

## Activation Inputs

Keep a second set of admin-managed activation files for app-specific setup:

- activation config JSON
  Example: Slack user ID, allowlist identifiers, product-specific toggles
- secrets env file
  Example: `OPENAI_API_KEY=...`

Suggested layout:

```text
/srv/openclaw/user-configs/alice.json
/srv/openclaw/activation-configs/alice.json
/srv/openclaw/activation-secrets/alice.env
```

Activate the user with:

```bash
sudo openclaw-hostctl activate-user alice \
  --activation-config /srv/openclaw/activation-configs/alice.json \
  --secrets-env /srv/openclaw/activation-secrets/alice.env \
  --restart
```

This persists host-side copies under the user VM directory and writes them into `/etc/openclaw/` inside the guest:

- `config.json`
- `activation.json`
- `secrets.env`

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
