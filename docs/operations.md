# Implementation Plan And Operations

## Implementation Plan

1. Create a DigitalOcean Droplet close to your users and choose a plan with dedicated CPU headroom for nested virtualization.
2. Choose a storage mode:
   - production mode: `/var/lib/openclaw` on XFS with reflink enabled or btrfs
   - cheap test mode: `/var/lib/openclaw` on the default Droplet root disk with `storage_copy_mode: copy`
3. Verify the Droplet exposes `/dev/kvm` before installing Firecracker.
4. Install Firecracker and place a compatible `vmlinux` kernel under `/var/lib/openclaw/base`.
5. Build the shared OpenClaw base image with [guest/build-base-image.sh](/Users/franciszhan/Documents/GitHub/openclaw-server/guest/build-base-image.sh).
6. Install the host control layer and stage the hardening artifacts with [bootstrap/install-host.sh](/Users/franciszhan/Documents/GitHub/openclaw-server/bootstrap/install-host.sh).
7. Validate the configuration with `openclaw-hostctl validate-config`.
8. Install Firecracker artifacts and build the guest base image.
9. Provision employees with `openclaw-hostctl provision`.
10. Boot and test one VM, then two VMs.
11. Apply host lockdown only after you have confirmed console access and working SSH from your admin IPs.
12. Monitor DigitalOcean maintenance events and restart nested guests after a Droplet live migration.

## DigitalOcean Host Choices

Recommended host shape:

- Debian 12 Droplet
- a region close to your team
- enough vCPU and RAM headroom for the host plus all guest VMs
- a plan that does not rely on heavily shared CPU for steady multi-user workloads

Storage options:

- Preferred: attach a dedicated DigitalOcean Volume, format it as XFS with reflink enabled, and mount it at `/var/lib/openclaw`
- Cheap first test: use the Droplet root disk and switch the host config to `storage_copy_mode: copy`

Preflight checks:

```bash
uname -a
ls -l /dev/kvm
egrep -c '(vmx|svm)' /proc/cpuinfo
```

If `/dev/kvm` is missing, stop there. Firecracker will not run correctly on that Droplet.

If you attach a separate Volume, a typical setup looks like:

```bash
sudo apt-get update
sudo apt-get install -y xfsprogs
sudo mkfs.xfs -m reflink=1 /dev/disk/by-id/scsi-0DO_Volume_openclaw-data
sudo mkdir -p /var/lib/openclaw
echo '/dev/disk/by-id/scsi-0DO_Volume_openclaw-data /var/lib/openclaw xfs defaults,noatime 0 2' | sudo tee -a /etc/fstab
sudo mount -a
sudo xfs_info /var/lib/openclaw | grep reflink
```

If you skip the extra Volume for the first test run, just create the directory on the root disk:

```bash
sudo mkdir -p /var/lib/openclaw
sudo chmod 0755 /var/lib/openclaw
df -h /var/lib/openclaw
```

## Host Install

Copy the repo to the host and run:

```bash
export ADMIN_CIDRS="198.51.100.10/32,198.51.100.11/32"
export PUBLIC_IFACE="enp5s0"
sudo ./bootstrap/install-host.sh
```

Then edit `/etc/openclaw/host-config.json` and confirm:

- `storage_root` points at your reflink-capable filesystem
- `base_rootfs` points at the built guest image
- `kernel_image` points at the Firecracker kernel
- `storage_copy_mode` is `copy` for a no-extra-volume test run on ext4, or `reflink` for the real reflink-backed setup
- `admin_ssh_keys_path` contains the host admin keys
- `shared_skills_dir` contains the approved shared skills you want copied into guests
- `allow_guest_egress` is `true` if guests need to call public LLM APIs directly; leave it `false` only if you plan to provide an internal relay or proxy

If you want a repo-managed starting set of company skills, sync the bundled files before building the base image:

```bash
sudo mkdir -p /var/lib/openclaw/shared-skills
sudo rsync -a /opt/openclaw-server/shared-skills/ /var/lib/openclaw/shared-skills/
```

DigitalOcean-specific recommendation:

- keep the Droplet Console available throughout the entire first run
- if you already use DigitalOcean Cloud Firewalls, treat them as an outer network guardrail and keep `nftables` on the host as the source of truth

Important behavior:

- `install-host.sh` now stages the firewall and SSH hardening files under `/etc/openclaw/lockdown/`
- it does not immediately enable `nftables`, `fail2ban`, or restart `sshd`
- this avoids locking you out before Firecracker and guest access are working

Inspect the staged files if you want:

```bash
sudo /usr/local/lib/openclaw/render-lockdown-config.sh
sudo ls -lah /etc/openclaw/lockdown
sudo nft -c -f /etc/openclaw/lockdown/nftables.conf
```

`render-lockdown-config.sh` is safe to rerun after any edit to `/etc/openclaw/host-config.json`. It refreshes the staged firewall config from the current host settings instead of leaving stale candidate files behind.

## Firecracker Artifacts

Before provisioning any guests, the host needs two separate artifacts:

- a Firecracker binary for the Droplet architecture
- an uncompressed Linux kernel image named `vmlinux` that is compatible with Firecracker

The commands shown later in this document assume you already have local files for both. `./firecracker` and `./vmlinux` are placeholders, not files created by this repo.

Check the host architecture first:

```bash
uname -m
```

Common failure modes in this step:

- `install: cannot stat './firecracker'`: you have not downloaded or built the Firecracker binary yet
- `install: cannot stat './vmlinux'`: you do not have a guest kernel yet
- `cannot execute binary file` or `Exec format error`: the Firecracker binary architecture does not match the Droplet architecture
- Firecracker starts but guest boot fails immediately: the kernel is not a compatible uncompressed `vmlinux`

For a first test run, prefer the official Firecracker CI guest kernel instead of building your own. It is the fastest way to avoid kernel config mistakes around virtio block, MMIO, and ext4 rootfs support.

Download the latest Firecracker CI kernel for the current architecture:

```bash
ARCH="$(uname -m)"
release_url="https://github.com/firecracker-microvm/firecracker/releases"
latest_version=$(basename "$(curl -fsSLI -o /dev/null -w %{url_effective} ${release_url}/latest)")
CI_VERSION="${latest_version%.*}"

latest_kernel_key=$(
  curl -fsSL "http://spec.ccfc.min.s3.amazonaws.com/?prefix=firecracker-ci/${CI_VERSION}/${ARCH}/vmlinux-&list-type=2" \
  | grep -oP "(?<=<Key>)(firecracker-ci/${CI_VERSION}/${ARCH}/vmlinux-[0-9]+\\.[0-9]+\\.[0-9]{1,3})(?=</Key>)" \
  | sort -V \
  | tail -1
)

mkdir -p /var/lib/openclaw/base
curl -fL "https://s3.amazonaws.com/spec.ccfc.min/${latest_kernel_key}" \
  -o /var/lib/openclaw/base/vmlinux

file /var/lib/openclaw/base/vmlinux
ls -lh /var/lib/openclaw/base/vmlinux
```

Only build your own kernel once the basic VM lifecycle is working and you are ready to own the guest kernel config.

## Base Image Build

The base image script installs a minimal Debian guest, creates an `admin` user, installs `udev`, enables SSH and `systemd-networkd`, and installs the guest first-boot unit.

Example:

```bash
export OPENCLAW_INSTALL_CMD='curl -fsSL https://internal.example/install-openclaw.sh | bash'
export SHARED_SKILLS_DIR=/var/lib/openclaw/shared-skills
sudo ./guest/build-base-image.sh
```

Expected follow-up:

- copy or build a Firecracker-compatible `vmlinux` to `/var/lib/openclaw/base/vmlinux`
- keep `/var/lib/openclaw/base/openclaw-base.ext4` immutable after validation

## Clean Reset

The cleanest reset path is to destroy the test Droplet and create a fresh one. That is less error-prone than trying to unwind partial `systemd`, firewall, and Firecracker state on a host you have already iterated on.

If you want to reuse the same Droplet instead, wipe the OpenClaw state and installed files first:

```bash
sudo systemctl stop 'openclaw-vm@*.service' || true
sudo pkill -f '/usr/local/bin/firecracker' || true
sudo systemctl disable --now nftables fail2ban openclaw-network.service || true
sudo rm -f /etc/systemd/system/openclaw-vm@.service
sudo rm -f /etc/systemd/system/openclaw-network.service
sudo rm -f /usr/local/bin/openclaw-hostctl
sudo rm -f /usr/local/lib/openclaw/openclaw-network-setup.sh
sudo rm -f /usr/local/lib/openclaw/apply-lockdown.sh
sudo rm -rf /opt/openclaw-host
sudo rm -rf /var/lib/openclaw
sudo rm -rf /mnt/openclaw
sudo rm -rf /etc/openclaw
sudo rm -f /etc/nftables.conf
sudo rm -f /etc/ssh/sshd_config.d/10-openclaw-hardening.conf
sudo rm -f /etc/fail2ban/jail.d/openclaw-sshd.local
sudo systemctl daemon-reload
sudo systemctl restart ssh
```

For a fresh Droplet, skip this section and continue with the normal install flow.

## Provision A User

Create one employee manifest file first. The provisioning flow stores it with the VM, writes only the non-OpenClaw metadata into `/etc/openclaw/config.json` inside the guest, and injects the approved shared skills.

See [employee-onboarding.md](/Users/franciszhan/Documents/GitHub/openclaw-server/docs/employee-onboarding.md) for the recommended intake checklist before provisioning.

```bash
sudo scripts/openclaw-hostctl \
  --config /etc/openclaw/host-config.json \
  provision alice \
  --display-name "Alice Example" \
  --user-config /srv/openclaw/user-configs/alice.json \
  --disk-size-gib 20
```

Provisioning does all of this:

- allocates the next free guest IP
- reflink-clones the base rootfs
- full-copies the base rootfs instead if `storage_copy_mode` is `copy`
- optionally expands the guest disk
- seeds hostname, guest network config, admin SSH keys, OpenClaw config, and shared skills
- writes a persistent user manifest
- creates an initial rollback snapshot

At start time, the host runtime path also recreates or reuses the guest tap device, attaches it to the private bridge, and enables bridge-port isolation so one guest cannot directly talk to another over `ocbr0`.

## Activate A User

Provisioning handles the VM and stores the full employee manifest. Activation applies the OpenClaw-specific parts of that manifest directly into the real OpenClaw state directory.

```bash
sudo openclaw-hostctl activate-user alice \
  --manifest /srv/openclaw/user-configs/alice.json \
  --restart
```

Activation does all of this:

- stops the VM first if it is running and you pass `--force`
- persists the latest manifest under the user VM directory for reuse
- mounts the guest disk offline
- writes `/home/admin/.openclaw/.env` from `openclaw.env`
- writes `/home/admin/.openclaw/openclaw.json` from the manifest OpenClaw config
- writes `/home/admin/.openclaw/agents/main/agent/auth-profiles.json` with env-backed API key refs
- writes `/home/admin/.openclaw/credentials/slack-default-allowFrom.json` from the Slack allowlist
- writes `/home/admin/.openclaw/shared-access.json` from the per-user shared access config
- defaults `agents.defaults.model.primary` to `openai/gpt-5.4` unless the manifest overrides it
- ensures `gateway.auth.token` exists
- installs and enables the user-level `openclaw-gateway.service`
- queues a guest-side gateway reload/restart on the next boot so the new config is live automatically
- enables linger for `admin`
- locks `/home/admin/.openclaw` and `/home/admin/.openclaw/credentials` down to `0700`
- installs `/usr/local/bin/openclaw-shared-access` for typed coordinator-only shared access
- installs `/usr/local/bin/company-email-intro-lookup` as the default owner-side shared-access command
- installs `/usr/local/bin/pkg-search` and `/usr/local/bin/pkg-install` for constrained guest package management without granting a general root shell
- optionally starts the VM again with `--restart`

This keeps the host-side workflow repeatable even if you need to reprovision or reapply the same employee-specific setup later.

Recommended post-activation check:

```bash
ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.10
openclaw status
```

Expect:

- Gateway reachable
- Slack `OK`
- OpenAI auth profile present
- no critical security warnings about missing gateway auth

If you modify OpenClaw config on a running guest after activation instead of re-running `activate-user --restart`, restart the gateway inside the guest:

```bash
openclaw gateway restart
```

Use that only for in-place config edits. A fresh `activate-user --restart` should already leave the gateway in the correct state.

## Coordinator Shared Access

The cross-agent Slack coordinator is a separate service with its own config and state root.

Example config:

```json
{
  "state_root": "/var/lib/openclaw/coordinator",
  "relay_command": [
    "ssh",
    "root@HOST_TAILSCALE_IP",
    "openclaw-hostctl",
    "shared-access",
    "execute",
    "{owner_vm_user_id}"
  ],
  "coordinator_slack_user_id": "UCOORDINATOR",
  "request_timeout_seconds": 60,
  "slack_bot_token": "xoxb-REPLACE_ME",
  "slack_app_token": "xapp-REPLACE_ME",
  "allow_self_requests_for_testing": false
}
```

The host bootstrap installs:

- `/usr/local/bin/openclaw-coordinatorctl`
- `openclaw-coordinator.service`

The Slack runtime needs `websocket-client`. `install-host.sh` installs it automatically on Debian hosts.

Minimal Slack app requirements for coordinator testing:

- Socket Mode enabled
- Interactivity enabled
- App Home Messages enabled
- app-level token with `connections:write`
- bot scopes:
  - `chat:write`
  - `app_mentions:read`
  - `im:history`
  - `im:write`
- events:
  - `app_mention`
  - `message.im`

Initialize state and register each opted-in owner manifest:

```bash
sudo openclaw-coordinatorctl --config /etc/openclaw/coordinator-config.json init-state
sudo openclaw-coordinatorctl --config /etc/openclaw/coordinator-config.json \
  directory upsert --manifest /srv/openclaw/user-configs/francis.json
```

Start the transport:

```bash
sudo systemctl enable --now openclaw-coordinator.service
sudo journalctl -u openclaw-coordinator.service -f
```

The host relay command:

```bash
sudo openclaw-hostctl shared-access execute alice < request.json
```

reads one typed shared-access request from stdin, SSHes into the target VM using the host automation key, runs `/usr/local/bin/openclaw-shared-access execute`, and prints sanitized JSON to stdout.

The coordinator should use this path only for typed lookup operations like:

- `email_intro_lookup`

If the per-user `shared_access` config does not override the command array, `email_intro_lookup` defaults to the built-in `company-email-intro-lookup` wrapper, which calls `openclaw agent --local` inside the owner VM and answers the specific request from the owner's email evidence within the default 1-year time window.

Do not use it to forward arbitrary prompts into another employee's general OpenClaw agent.

## Start, Stop, And Inspect

```bash
sudo openclaw-hostctl start alice
sudo openclaw-hostctl status alice
sudo openclaw-hostctl stop alice
```

The microVM runs under `systemd` as `openclaw-vm@alice.service`.

Host-side inspection:

```bash
sudo systemctl status openclaw-vm@alice.service
sudo journalctl -u openclaw-vm@alice.service
```

Guest access for a human admin should normally come from your workstation through the Droplet as a jump host:

```bash
ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.10
```

Direct `ssh admin@172.31.0.10` from the Droplet itself only works if you also place the matching private key on the host. This repo does not do that by default.

For the first run, start one guest before starting the second:

```bash
sudo openclaw-hostctl start alice
sudo journalctl -u openclaw-vm@alice.service -n 120 --no-pager
ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.10

sudo openclaw-hostctl start bob
sudo journalctl -u openclaw-vm@bob.service -n 120 --no-pager
ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.11
```

This makes it obvious whether you have a general boot problem or a second-VM runtime problem.

If `allow_guest_egress` is true, `openclaw-network.service` now installs a small runtime masquerade rule before lockdown is applied. This keeps direct LLM calls working during the first-run smoke test. The permanent NAT rule still comes from the staged `nftables` policy once you apply lockdown.

## Apply Lockdown

Only apply host lockdown after all of these are true:

- you can still reach the Droplet Console
- your admin IPs in `ADMIN_CIDRS` are correct
- `alice` and `bob` both boot successfully
- you can SSH from your workstation into both guests through the Droplet jump host

Then apply the staged hardening:

```bash
sudo /usr/local/lib/openclaw/apply-lockdown.sh
sudo systemctl status nftables fail2ban --no-pager
sudo nft list ruleset
```

Before you close the console, verify SSH from a second terminal on your workstation.
If guest access matters immediately, also re-run one `ssh -J ... admin@172.31.0.10` test after lockdown so you confirm the jump-host path still works.

## First Two-VM Test

Recommended first test order:

1. `openclaw-hostctl validate-config`
2. provision `alice`
3. start `alice`
4. verify `ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.10`
5. provision `bob`
6. start `bob`
7. verify `ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.11`
8. test guest-to-guest isolation from inside `alice`
9. test one prompt from inside `alice` so you confirm LLM egress works
10. test one snapshot and restore on `alice`
11. apply lockdown

For a persistence check before you trust the environment, make a user-visible change inside the guest, stop the VM, start it again, and confirm the state remains:

```bash
ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.10 'echo persistent-test > ~/persist.txt'
sudo openclaw-hostctl stop alice
sudo openclaw-hostctl start alice
ssh -J root@YOUR_DROPLET_PUBLIC_IP admin@172.31.0.10 'cat ~/persist.txt'
```

Disk-backed state should survive stop/start cycles. Live in-memory session state does not, because this platform does not use memory snapshots.

## Optional Tailscale Host Access

Tailscale on the host is a good fit for this design. It does not break Firecracker, bridge networking, or `ProxyJump` access to guests as long as you keep the Tailscale scope narrow.

Recommended usage:

- install Tailscale on the Droplet host only
- use the Tailscale IP or MagicDNS name as the `ProxyJump` target instead of the public IP
- keep the guest bridge private and do not advertise `172.31.0.0/24` as a tailnet subnet route unless you intentionally want tailnet-wide guest reachability

The staged firewall already allows SSH arriving on `tailscale0`, so standard SSH over Tailscale works after lockdown without opening additional public inbound paths.
It also allows inbound UDP `41641` on the public interface so `tailscaled` can keep the host reachable from the tailnet after lockdown.

## Snapshot And Restore

Take snapshots only while the guest is stopped. This keeps the rollback point simple and filesystem-consistent.

Create:

```bash
sudo openclaw-hostctl stop alice
sudo openclaw-hostctl snapshot create alice before-upgrade
```

List:

```bash
sudo openclaw-hostctl snapshot list alice
```

Restore:

```bash
sudo openclaw-hostctl snapshot restore alice 20260310T180000Z-before-upgrade
sudo openclaw-hostctl start alice
```

`restore` automatically creates a `pre-restore` safety snapshot before replacing the active disk.

## DigitalOcean Maintenance Events

DigitalOcean can live-migrate a Droplet during maintenance. If you run nested Firecracker guests, treat that as a host maintenance event and restart the guest microVMs afterward.

Check for a pending event from the Droplet itself:

```bash
curl http://169.254.169.254/v1/maintenance_event
```

If this returns `live_migrate`, plan for a host maintenance window. After the migration completes, restart each user VM:

```bash
sudo openclaw-hostctl status
sudo systemctl restart 'openclaw-vm@*.service'
```

If you want a more conservative path, stop all user VMs before the migration window and start them again after the host stabilizes.

## Directory Layout

Important host paths:

- `/etc/openclaw/host-config.json`
- `/etc/openclaw/admin_authorized_keys`
- `/var/lib/openclaw/base/`
- `/var/lib/openclaw/vms/<user>/vm.json`
- `/var/lib/openclaw/vms/<user>/rootfs.ext4`
- `/var/lib/openclaw/vms/<user>/runtime/firecracker.json`
- `/var/lib/openclaw/vms/<user>/snapshots/`

## Day-2 Notes

- Rebuild the base image when OpenClaw or the approved skill set changes materially.
- Existing users stay pinned to their provisioned disk state until you migrate them deliberately.
- Back up `/var/lib/openclaw` at the filesystem level if you need off-host disaster recovery.
- Treat snapshots as local rollback points, not as a replacement for host backups.
- Review Droplet history and maintenance notifications after any provider-side event before assuming guest state is healthy.
- Move to reflink-backed storage before treating this as the steady-state production layout.
