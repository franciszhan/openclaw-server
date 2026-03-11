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
