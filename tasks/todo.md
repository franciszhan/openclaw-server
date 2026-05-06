# OpenClaw Host Platform v0

## Plan

- [x] Define the architecture, storage model, and networking model for a single-host Firecracker deployment.
- [x] Implement the `openclaw-hostctl` control CLI for provision, start, stop, status, snapshot, and restore.
- [x] Add host bootstrap artifacts for network setup, systemd integration, firewalling, and baseline hardening.
- [x] Add guest image build artifacts for a minimal OpenClaw appliance base image.
- [x] Write operator documentation for installation, provisioning, rollback, and extension.
- [x] Verify the locally testable code paths and capture operational limitations.

## Review

- Verified with `make test`.
- Verified with `make validate`.
- Verified Python import/parse integrity with `python3 -m compileall src`.
- Verified on a live DigitalOcean Droplet that rebuilt guests boot under Firecracker, acquire their private bridge IPs, accept SSH via `ProxyJump`, isolate east-west traffic, and restore from a disk snapshot.
- Root causes fixed during live validation: missing guest DNS seeding during build-time installs, missing `systemd-resolved`, missing `udev` in the appliance image, and a first-run egress gap before staged `nftables` lockdown was applied.

## Follow-Up

- [x] Add a test-mode storage fallback for non-reflink filesystems so a cheap DigitalOcean smoke test can run on the default root disk.
- [x] Verify the rebuilt guest image enables `systemd-networkd`, `systemd-resolved`, `ssh`, and `openclaw-firstboot` via `systemctl --root`.
- [x] Reprovision and boot `alice`, then confirm host-to-guest ping and SSH over the private bridge.
- [x] Reprovision and boot `bob`, then confirm guest isolation, persistence, and snapshot/restore on the live Droplet.

## Production Bring-Up

- [x] Verify the new production Droplet prerequisites and mount the attached XFS volume at `/var/lib/openclaw`.
- [x] Sync the repo, run bootstrap, and validate the host configuration on the new Droplet.
- [x] Install Firecracker and the official guest kernel, then build the shared base image on the new Droplet.
- [x] Provision and validate the first production test VM, including SSH, egress, and persistence.
- [ ] Install Tailscale on the host and complete tailnet enrollment, then document the final operator access pattern.

## Next Feature

- [x] Inspect the existing per-user profile flow and determine whether it can back a reusable activation step.
- [x] Add an `activate-user` command that persists activation inputs and reapplies them into an existing guest.
- [x] Document the provision-vs-activate workflow and verify it with automated tests.

## AgentCoordinator DM Test Bring-Up

- [x] Extend coordinator config, parsing, and policy to support DM entrypoints and an explicit self-test mode that targets a configured owner.
- [x] Add a Slack Socket Mode transport/runtime for the coordinator with approval, review, and draft actions.
- [x] Document the AgentCoordinator Slack app and prod host test setup.
- [x] Verify the coordinator locally with unit tests and compile checks.
- [x] Wire the prod host with coordinator config for the Francis DM test path.

## OpenClaw Update Sweep

- [x] Inspect per-VM OpenClaw versions and recent activity to identify idle VMs.
- [x] Update only VMs that appear idle and leave active ones untouched.
- [x] Verify post-update versions and record any VMs intentionally skipped.

## Update Sweep Review

- Verified pre-update state across 15 VMs: all were on `OpenClaw 2026.3.12 (6472949)`.
- Verified low recent activity before update: no workspace/state/session file churn in the prior 30 minutes; only baseline gateway socket reconnect log lines.
- The built-in `openclaw update --yes` path failed under the `admin` user because the install is global under `/usr/lib/node_modules/openclaw` and `npm i -g` hit `EACCES`.
- Successful update path: `sudo npm i -g openclaw@latest --no-fund --no-audit --loglevel=error`, then `systemctl --user restart openclaw-gateway.service`.
- Verified post-update state across all 15 VMs: `OpenClaw 2026.4.8 (9ece252)` and gateway `active`.

## Google Broker Drift Audit

- [x] Trace every `alice` reference in runtime code, tests, and docs to separate benign examples from real broker-state risks.
- [x] Remove broker refresh/prune side effects from broker directory setup so provisioning cannot mutate auth state implicitly.
- [x] Add regression tests covering broker directory setup and stale-orphan pruning with neutral fixture names.
- [x] Sync the host-side fix to prod and verify the broker inventory remains aligned with live VM users.

## Google Broker Drift Review

- Root cause 1: `_ensure_google_oauth_broker_directories()` refreshed and pruned broker auth state as a side effect, so innocuous broker setup during provisioning could mutate live auth inventory.
- Root cause 2: `HostConfig` did not carry the broker root path, so prod host tests instantiated temp configs while still writing real broker state under `/var/lib/openclaw/google-oauth-broker`.
- Benign `alice` references remain in some historical docs/tests as examples only; the runtime regression came from the hardcoded broker root plus side-effectful refresh path, not from example strings by themselves.
- Verified locally with `PYTHONPATH=src python3 -m unittest tests.test_hostctl` and `python3 -m compileall src/openclaw_hostctl tests/test_hostctl.py`.
- Synced host-side fixes to prod, added explicit `google_oauth_broker_root` to `/etc/openclaw/host-config.json`, and verified the prod host tests no longer mutate live broker state.
- Rebuilt the prod broker inventory with `openclaw-hostctl google-auth reconcile`, verified `alice` is gone from host broker state, and confirmed `google-auth-status` succeeds again on both `francis` and `lauren`.

## AgentCoordinator DM-Only Update

- [x] Map current coordinator, Slack transport, parser, and shared-access helper behavior.
- [x] Switch new Slack requests to DM-only and route requester-visible updates back to the requester DM.
- [x] Remove coordinator-side email lookup content filters so owner approval/review is the gate.
- [x] Relax requester-side directory guardrails while preserving owner routing required for execution.
- [x] Update tests, config examples, and docs for the DM-only workflow.
- [x] Verify with targeted coordinator tests, Slack transport tests, full unit tests, and compile checks.

## AgentCoordinator DM-Only Review

- New Slack requests now start from coordinator DMs, public app mentions only send private DM guidance, and requester-visible lifecycle messages stay in the requester DM.
- Coordinator-side content filters for broad mailbox, raw email, sensitive-topic, and non-lookup phrasing were removed; owner approval is the gate.
- Requesters no longer need a coordinator directory entry; the owner still must resolve to a directory entry so the coordinator can DM and execute against the owner VM.
- Generated shared-access config no longer includes default recipient filters, and the built-in email lookup prompt no longer imposes the old 1-year, attachment-count, or raw-content wording.
- Verified with `PYTHONPATH=src python3 -m unittest tests.test_coordinator tests.test_slack_transport`, `make test`, `PYTHONPATH=src python3 -m compileall src tests`, and `make validate`.

## AgentCoordinator Single-Approval Update

- [x] Publish email lookup results to the requester immediately after owner approval and successful execution.
- [x] Stop emitting new owner review actions/buttons for email lookup requests.
- [x] Keep existing review command handling compatible for any old `owner_review_pending` records.
- [x] Update tests and docs for the single owner approval workflow.
- [x] Verify with targeted coordinator tests, full tests, compile checks, and validation.

## AgentCoordinator Single-Approval Review

- Owner approval now executes the email lookup and immediately transitions the request to `published`.
- Successful approvals emit `requester_dm_published` directly instead of `owner_dm_review`.
- Existing `record_owner_review` and review Slack actions remain available for older saved `owner_review_pending` requests.
- Verified with `PYTHONPATH=src python3 -m unittest tests.test_coordinator`, `PYTHONPATH=src python3 -m unittest tests.test_coordinator tests.test_slack_transport`, `make test`, `PYTHONPATH=src python3 -m compileall src tests`, and `make validate`.

## AgentCoordinator DM Phrase Fix

- [x] Add a regression for `hey spark, can you check @Francis Zhan email for the latest updates on EDG?`.
- [x] Parse `latest updates on <entity>` before generic `email for <entity>` so the fallback entity is `EDG`.
- [x] Verify with `PYTHONPATH=src python3 -m unittest tests.test_coordinator`, `make test`, `PYTHONPATH=src python3 -m compileall src tests`, and `make validate`.

## AgentCoordinator Content Approval Fix

- [x] Move email lookup execution to request submission so the owner approval includes generated content.
- [x] Change owner approval to publish the stored result without running the lookup again.
- [x] Update Slack approval blocks to include the generated result and label the primary action `Approve & Send`.
- [x] Update docs to describe content approval rather than pre-run approval.

## AgentCoordinator Immediate Requester Ack

- [x] Split request submission from queued lookup execution so Spark can acknowledge the requester before the lookup starts.
- [x] Trigger the queued lookup after posting the requester ack, then DM the generated content to the owner for approval.
- [x] Add transport coverage that requester ack is posted before owner approval preparation runs.
- [x] Verify with full tests, compile checks, config validation, and whitespace checks.

## AgentCoordinator Immediate Requester Ack Review

- Successful DM requests now immediately tell the requester the lookup is queued for owner approval before the lookup execution begins.
- Lookup execution still prepares the generated content before owner approval, so the single owner approval sends that already-reviewed content to the requester.
- Verified locally with `make test`, `PYTHONPATH=src python3 -m compileall src tests`, `make validate`, and `git diff --check`.

## Shared Access Error Propagation Fix

- [x] Reproduce the prod failure behind request `239a388e5e2d`.
- [x] Confirm the actual guest-helper failure was `lookup returned no supporting references`.
- [x] Propagate shared-access helper stderr through `openclaw-hostctl shared-access execute`.
- [x] Add regression coverage so coordinator failure classification receives the real helper reason.
- [x] Verify with targeted tests, full tests, compile checks, config validation, and whitespace checks.

## Shared Access Gmail Preflight Fix

- [x] Inspect latest Jonathan request `3c006c257cf6` and confirm it targeted `jonathan` for `Carta`.
- [x] Check Jonathan VM, shared-access config, and Google auth health without reading mailbox contents.
- [x] Confirm Jonathan's Google auth is not connected, causing the lookup to return no supporting references.
- [x] Add a Gmail connection preflight to the owner-side email lookup helper.
- [x] Add a host-relay Gmail connection preflight so existing VMs get the clearer failure without guest refresh.
- [x] Classify disconnected Gmail separately from true empty email-search results.
- [x] Verify with targeted tests, full tests, compile checks, config validation, and whitespace checks.

## Shared Access Gateway Pairing Fix

- [x] Inspect latest Jon request `56e5f03354e7` and confirm it targeted `jon` for `ejj capital`.
- [x] Confirm Jon's Google auth is connected, so the failure was not Gmail OAuth.
- [x] Inspect Jon's OpenClaw session metadata and identify `pairing required` gateway failures during local email access.
- [x] Add host-relay and guest-helper preflights for `operator.admin` gateway pairing.
- [x] Classify gateway pairing failures separately from true empty email-search results.
- [x] Ensure guest email lookups use a fresh per-request OpenClaw session id instead of reusing contaminated main-agent history.
- [x] Refresh expired guest Gmail access tokens from the host broker before shared email execution.
- [x] Verify with targeted tests, full tests, compile checks, config validation, and whitespace checks.

## Google Auth Automatic Refresh

- [x] Confirm Google access-token and refresh-token expiry behavior from official docs.
- [x] Add explicit `google-auth refresh` and `google-auth refresh-all` hostctl commands.
- [x] Keep refresh automatic at lookup time rather than adding a recurring timer.
- [x] Disable and remove the temporary prod refresh timer.
- [x] Verify with targeted tests, full tests, compile checks, config validation, and whitespace checks.

## Google Auth Automatic Refresh Review

- Google access tokens expire quickly; connected owner tokens are now refreshed at lookup/status time before shared email execution.
- `google-auth refresh` and `google-auth refresh-all` are available as explicit operator commands, but no recurring background timer is installed.
- The temporary prod timer was disabled and removed after the scope change.
- Verified locally with targeted tests, `make test`, `PYTHONPATH=src python3 -m compileall src tests`, `make validate`, and `git diff --check`.

## Direct Email Prompt Auth Refresh

- [x] Update VM agent instructions so direct Gmail/email prompts run `google-auth-status` before using Google Workspace data.
- [x] Make the company AGENTS addendum refresh replace stale instructions instead of leaving old addenda in place.
- [x] Push the updated addendum to existing prod VMs.
- [x] Verify locally with targeted tests, full tests, compile checks, config validation, and whitespace checks.
- [x] Verify on prod.

## Direct Email Prompt Auth Refresh Review

- Direct owner-agent prompts now instruct the agent to run `google-auth-status` before Gmail/email/inbox or other Google Workspace access.
- `google-auth-status` already refreshes expired access tokens through the host broker before returning `connected: true`.
- The AGENTS addendum updater now replaces stale addenda, so existing instructions can evolve instead of being permanently skipped by the marker.
- Updated and verified the instruction across all 15 active prod VMs.

## OpenClaw And Codex 5.5 Sweep

- [x] Inventory current OpenClaw versions and default models across prod VMs.
- [x] Update OpenClaw inside each VM.
- [x] Move VMs still on the 5.4 default model to `openai/gpt-5.5`.
- [x] Restart and verify each OpenClaw gateway.
- [x] Record final versions, models, and any exceptions.

## OpenClaw And Codex 5.5 Sweep Review

- Pre-sweep state: all 15 prod VMs were active on `OpenClaw 2026.4.8 (9ece252)` with `agents.defaults.model.primary` set to `openai/gpt-5.4`.
- Verified the latest npm tag from a guest as `openclaw@2026.5.3-1`, then updated each VM with `sudo npm i -g openclaw@latest --no-fund --no-audit --loglevel=error`.
- Updated every VM still on 5.4 to `openai/gpt-5.5`, preserving the existing provider route instead of switching unauthenticated VMs to the `openai-codex` OAuth route.
- Restarted every `openclaw-gateway.service` and independently verified all 15 VMs now report `OpenClaw 2026.5.3-1 (2eae30e)`, model `openai/gpt-5.5`, and gateway `active`.

## Per-VM OpenAI Key Migration

- [x] Inventory existing project keys and confirm no `openclaw-*` service accounts existed.
- [x] Create per-VM OpenAI project service accounts and install their generated API keys into VM `.env` files.
- [x] Move the coordinator intent extractor to its own OpenAI project service-account key.
- [x] Restart and verify every VM gateway with a non-content OpenAI `/v1/models` auth check.
- [x] Store a non-secret service-account/key-id mapping on prod.
- [x] Remove temporary VM `.env.backup-*` files that contained the old shared key.
- [ ] Revoke the temporary admin key and old shared `test-key` after operator review.

## Lauren Compliance Context Rollout

- [x] Review the new Lauren compliance agent context file.
- [x] Mark the context active and add explicit operating instructions for Lauren's OpenClaw.
- [ ] Sync the context into Lauren's VM. Blocked 2026-05-06: prod SSH to `100.95.220.93` timed out.
- [ ] Add a Lauren-specific `AGENTS.md` instruction pointing to the context. Blocked until prod SSH is reachable.
- [ ] Verify Lauren's VM can see the context without exposing the file contents in logs. Blocked until prod SSH is reachable.
