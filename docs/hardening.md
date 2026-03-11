# Host Hardening Setup

The hardening in this repo is intentionally baseline-focused. It locks down the single host enough for an internal v0 deployment without introducing a heavyweight compliance framework.

The operational rule is important: for a first deployment, stage the hardening during install and apply it only after the host, Firecracker, and guest SSH paths are confirmed.

## Implemented Controls

- key-only SSH with [bootstrap/sshd-hardening.conf](/Users/franciszhan/Documents/GitHub/openclaw-server/bootstrap/sshd-hardening.conf)
- root SSH login disabled
- narrow admin access via explicit `ADMIN_CIDRS` rendered into `nftables`
- default-deny inbound firewall
- fail2ban for SSH
- unattended security updates
- conservative IPv4 and kernel sysctl hardening
- persistent journald storage
- restricted `/etc/openclaw` permissions
- no public guest-facing services by default
- host SSH still allows local TCP forwarding so workstation `ProxyJump` access to guests continues to work after lockdown

## Firewall Behavior

Rendered `nftables` policy:

- default drop on inbound and forwarded traffic
- allow established and loopback traffic
- allow host SSH only from configured admin CIDRs
- allow host access from the private OpenClaw bridge
- rely on bridge port isolation so guest ports on the host bridge cannot directly exchange frames
- drop guest-to-guest forwarding
- optionally masquerade guest egress to the public interface

This gives a simple outcome:

- host admin traffic is explicit
- guests are private
- guests cannot directly talk east-west over the bridge
- guest public exposure is absent unless an operator changes the firewall intentionally

## Required Manual Review

Before using this on the real DigitalOcean Droplet, verify:

- the public interface name passed to `install-host.sh`
- the admin CIDRs allowed through SSH
- that the host still exposes no public listener other than SSH
- that `/var/lib/openclaw` is on a reflink-capable filesystem for production, or that `storage_copy_mode` is intentionally set to `copy` for a small test run
- that `/dev/kvm` exists inside the Droplet
- that Firecracker and the kernel image paths are correct
- that `/etc/openclaw/admin_authorized_keys` is populated
- that you retain Droplet Console access while tightening SSH and firewall rules

## Application Order

Safe first-run order:

1. install the host packages and staged configs with `install-host.sh`
2. validate Firecracker, the guest kernel, and the base image
3. provision and boot one guest
4. provision and boot a second guest
5. verify SSH and rollback behavior
6. apply the staged lockdown with `/usr/local/lib/openclaw/apply-lockdown.sh`

This sequencing is intentional. It prevents the host firewall and SSH lockdown from becoming part of the debugging surface while you are still bringing up the first microVMs.

## Suggested Extra Controls

These are reasonable next steps, but intentionally not built into v0:

- put the Droplet on Tailscale and prefer admin SSH over the tailnet instead of relying only on public-IP CIDR filtering
- do not advertise the guest bridge (`172.31.0.0/24` by default) as a Tailscale subnet route unless you explicitly want tailnet devices to reach guest VMs
- use the Firecracker `jailer` once operational workflows are stable
- pin outbound guest destinations if OpenClaw only needs a narrow set of egress paths
- ship host logs to a remote sink
- store off-host backups of `/var/lib/openclaw`
- add guest disk encryption if the threat model requires it
- add a small maintenance hook for DigitalOcean live migration detection and post-migration guest restarts
