# Lessons

- Do not let tests or temp configs write into hardcoded global runtime paths. Any persistent host-side state, especially auth inventories like the Google broker root, must come from `HostConfig` so tests stay sandboxed.
- Do not hide destructive auth-state refreshes inside setup helpers. Directory/user ensure paths must be side-effect free with respect to live credentials; explicit reconcile/refresh paths should own pruning.
- For shared email requests, the single owner approval must include the generated lookup content. Approval should publish stored content, not approve a future lookup or trigger a second review step.
- For Slack DM request workflows, acknowledge a successfully accepted request before any long-running lookup starts so the requester immediately knows Spark queued it.
- Shared-access relay CLIs must preserve guest-helper stderr on failure; otherwise coordinator classifiers lose actionable failure reasons and fall back to vague requester messages.
- Shared email lookup helpers must preflight owner Gmail connectivity before asking the agent to search; disconnected auth should never be classified as an empty result.
- Shared email lookup helpers must preflight local OpenClaw gateway `operator.admin` pairing; gateway pairing failures are operational access failures, not empty email-search results.
- Shared email lookup helpers must run each request in a fresh per-request OpenClaw session; reusing a persistent agent session can contaminate future lookups with prior failures.
- Host-side Google auth status must refresh expired guest access tokens before reporting a VM as connected for shared email lookup.
- Google access tokens are short-lived; shared Gmail auth should refresh automatically at lookup time so connected users do not fail because an access token aged out.
- Direct user prompts that need Gmail/email data must also run the same Google auth status helper before access; coordinator-only refresh is not enough.
- When changing OpenClaw model defaults, preserve the existing provider/auth route unless the target route is known to be authenticated on every VM.
- For per-user OpenAI usage attribution, use one project service-account key per VM and store only non-secret ids centrally; generated key values should live only in each VM's `.env`.
- Host-triggered OpenClaw workflows that must continue across normal Slack DM turns should not rely only on `AGENTS.md`; prompt cache boundaries can leave active sessions with stale instructions, so persist explicit state and use an internal hook for deterministic continuation.
- For chat workflows, only one component should own visible follow-up delivery. If an internal hook advances state while the agent also processes the turn, the hook should not also send Slack messages unless the agent response is explicitly suppressed.
- For long-idle chat workflows, record-only hooks need a delayed fallback when the agent does not visibly ask the expected next question; otherwise stale sessions can acknowledge work without continuing the flow.
