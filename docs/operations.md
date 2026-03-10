# Implementation Plan And Operations

## Implementation Plan

1. Create a DigitalOcean Droplet close to your users and choose a plan with dedicated CPU headroom for nested virtualization.
2. Choose a storage mode:
   - production mode: `/var/lib/openclaw` on XFS with reflink enabled or btrfs
   - cheap test mode: `/var/lib/openclaw` on the default Droplet root disk with `storage_copy_mode: copy`
3. Verify the Droplet exposes `/dev/kvm` before installing Firecracker.
4. Install Firecracker and place a compatible `vmlinux` kernel under `/var/lib/openclaw/base`.
5. Build the shared OpenClaw base image with [guest/build-base-image.sh](/Users/franciszhan/Documents/GitHub/openclaw-server/guest/build-base-image.sh).
6. Install the host control layer and hardening artifacts with [bootstrap/install-host.sh](/Users/franciszhan/Documents/GitHub/openclaw-server/bootstrap/install-host.sh).
7. Validate the configuration with `openclaw-hostctl validate-config`.
8. Provision employees with `openclaw-hostctl provision`.
9. Operate each employee VM with `start`, `stop`, `status`, `snapshot`, and `restore`.
10. Monitor DigitalOcean maintenance events and restart nested guests after a Droplet live migration.

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

DigitalOcean-specific recommendation:

- keep the Droplet Console available while applying SSH and firewall changes
- if you already use DigitalOcean Cloud Firewalls, treat them as an outer network guardrail and keep `nftables` on the host as the source of truth

## Base Image Build

The base image script installs a minimal Debian guest, creates an `admin` user, enables SSH and `systemd-networkd`, and installs the guest first-boot unit.

Example:

```bash
export OPENCLAW_INSTALL_CMD='curl -fsSL https://internal.example/install-openclaw.sh | bash'
export SHARED_SKILLS_DIR=/srv/openclaw/company-skills
sudo ./guest/build-base-image.sh
```

Expected follow-up:

- copy or build a Firecracker-compatible `vmlinux` to `/var/lib/openclaw/base/vmlinux`
- keep `/var/lib/openclaw/base/openclaw-base.ext4` immutable after validation

## Provision A User

Create an employee-specific OpenClaw config file first. The provisioning flow writes it into `/etc/openclaw/config.json` inside the guest and injects the approved shared skills.

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

Guest access from the host:

```bash
ssh admin@172.31.0.10
```

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
