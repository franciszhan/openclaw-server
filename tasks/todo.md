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
