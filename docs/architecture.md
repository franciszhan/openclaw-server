# Architecture Summary

## Scope

This v0 design targets one DigitalOcean Droplet and one Firecracker microVM per opted-in employee.

It deliberately does not try to become a general cloud platform. The admin model is host-centric, explicit, and small enough to reason about under operational pressure.

## Main Components

- Host OS: Debian or Ubuntu on the DigitalOcean Droplet, hardened and running Firecracker, `systemd`, `nftables`, and the `openclaw-hostctl` CLI.
- Shared immutable guest base: one ext4 guest image file plus one Firecracker-compatible `vmlinux` kernel stored under `/var/lib/openclaw/base`.
- Per-user writable state: one per-user ext4 root filesystem under `/var/lib/openclaw/vms/<user>/rootfs.ext4`, created with reflinks in production or full copies in a cheap test run.
- Snapshot storage: one directory per snapshot under `/var/lib/openclaw/vms/<user>/snapshots/<timestamp>-<label>/`.
- Private network: one bridge (`ocbr0` by default) with static guest IPs, host-only reachability by default, and optional guest egress NAT with no public inbound exposure.
- Process supervision: one `openclaw-vm@<user>.service` systemd unit per running microVM.

## Storage Model

The storage model is intentionally simple:

1. Build a minimal OpenClaw appliance base image once.
2. Keep that base image immutable.
3. Provision each employee by cloning the base image into their own rootfs file.
4. Seed their hostname, static network config, admin SSH keys, approved shared skills, and per-user OpenClaw config before first boot.
5. Create disk-only snapshots by cloning the user rootfs into a timestamped snapshot directory.
6. Restore by stopping the VM, taking a `pre-restore` safety snapshot, and replacing the active rootfs file with a clone of the chosen snapshot.

Production mode (`storage_copy_mode: reflink`) means:

- unchanged blocks stay shared on the host filesystem
- rollbacks are cheap and fast
- no guest memory snapshots are involved
- base image updates are explicit instead of magically mutating running guests

Cheap test mode (`storage_copy_mode: copy`) means:

- the same lifecycle still works on a normal ext4 root disk
- provisioning and snapshots consume full disk space
- operations are slower, but acceptable for a two-VM smoke test

## Isolation Model

Primary isolation boundary:

- KVM-backed Firecracker microVM boundary per employee

Additional guardrails:

- one private tap interface per VM
- bridge port isolation enabled on each tap
- no guest-to-guest forwarding on the bridge
- no public guest listeners exposed directly
- root SSH disabled on the host
- key-only admin access
- restrictive host firewall

This repo intentionally uses Firecracker directly under `systemd` rather than layering in a more complex control plane or jailer-based management flow for v0. The tradeoff is less moving parts and easier recovery at the expense of a tighter but less feature-rich sandboxing story around the Firecracker process itself.

## Networking

Default network layout:

- host bridge: `172.31.0.1/24`
- guest pool: `172.31.0.10-172.31.0.250`
- one static IP per guest
- SSH to guests only over the private bridge, typically via `ProxyJump` through the Droplet from an admin workstation
- no inbound routing from the public internet to guests
- optional egress NAT for guests if `allow_guest_egress` is true

Direct SaaS model access from inside the guest depends on that egress path. If `allow_guest_egress` is false, OpenClaw needs an internal model proxy or another host-side relay instead of calling the provider APIs directly.

`nftables` drops guest-to-guest forwarding, so the only normal communication paths are:

- host -> guest
- guest -> host
- guest -> internet via host NAT, if enabled

## Operational Assumptions

- Production: `/var/lib/openclaw` lives on XFS with reflink enabled or on btrfs.
- Cheap test run: `/var/lib/openclaw` may live on the default Droplet ext4 root disk if `storage_copy_mode` is set to `copy`.
- The Droplet exposes `/dev/kvm` and nested virtualization works in the chosen region and plan.
- The Droplet is sized with headroom for nested virtualization overhead; avoid the smallest shared-CPU plans for multi-user usage.
- If the Droplet root filesystem is not suitable for reflink-backed storage, attach a separate DigitalOcean Volume and mount it at `/var/lib/openclaw`.
- Firecracker is installed separately at `/usr/local/bin/firecracker`.
- A Firecracker-compatible `vmlinux` is placed at the configured path.
- The host admin maintains `/etc/openclaw/admin_authorized_keys`.
- OpenClaw installation inside the guest is handled at base-image build time through `OPENCLAW_INSTALL_CMD`.
- DigitalOcean may live-migrate the Droplet for maintenance; nested Firecracker guests should be restarted after such an event.

## Known v0 Limits

- Snapshots are disk-only and require the VM to be stopped for a clean rollback point.
- There is no self-service UI.
- There is no live migration or memory-state capture.
- Provider-initiated Droplet live migrations are outside the control plane and must be handled operationally.
- Base-image upgrades do not automatically rebase existing users.
- Guest customization is file-based and simple by design.

## Cross-Agent Shared Access

The cross-agent Slack workflow is intentionally brokered instead of letting one employee's full agent call another employee's full agent directly.

Components:

- `AgentCoordinator`: a separate Slack-facing service with its own app credentials and file-backed request state
- host relay: `openclaw-hostctl shared-access execute <user_id>`
- owner-side scoped runner inside each opted-in VM: `/usr/local/bin/openclaw-shared-access`
- Slack transport: a Socket Mode loop that maps Slack events and button actions onto coordinator state transitions

Execution path:

1. A requester asks in a public Slack thread.
2. The coordinator parses the request into a typed operation.
3. The owner approves or rejects in DM.
4. The coordinator calls the host relay.
5. The host relay SSHes into the owner VM with a host-managed automation key.
6. The owner VM runs only the typed shared-access helper, not the main personalized OpenClaw agent.
7. The owner reviews the result once more before publication.

For one-user DM testing only, the coordinator can be placed into an explicit self-test mode. In that mode, a DM can default to a configured owner Slack ID and bypass the usual requester-versus-owner separation check. Keep that disabled outside test runs.

Security properties:

- default deny
- opt-in per owner
- typed capabilities only
- no raw email body export by default
- no direct guest-to-guest network dependency
- coordinator can live off-host because the host relay terminates the private VM access path
