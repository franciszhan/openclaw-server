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
- Not verified here: actual Firecracker boot, loop-mount guest seeding, nftables application, or systemd unit execution on a real host.

## Follow-Up

- [x] Add a test-mode storage fallback for non-reflink filesystems so a cheap DigitalOcean smoke test can run on the default root disk.
