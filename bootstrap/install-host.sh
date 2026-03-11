#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
INSTALL_ROOT=${INSTALL_ROOT:-/opt/openclaw-host}
CONFIG_DEST=${CONFIG_DEST:-/etc/openclaw/host-config.json}
PUBLIC_IFACE=${PUBLIC_IFACE:-$(ip route show default | awk '/default/ {print $5; exit}')}
ADMIN_CIDRS=${ADMIN_CIDRS:-}
LOCKDOWN_CANDIDATE_DIR=/etc/openclaw/lockdown

if [[ -z "${ADMIN_CIDRS}" ]]; then
  echo "set ADMIN_CIDRS to a comma-separated list of allowed admin networks" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  fail2ban \
  nftables \
  python3 \
  rsync \
  unattended-upgrades

mkdir -p /etc/openclaw "${LOCKDOWN_CANDIDATE_DIR}" /usr/local/bin /usr/local/lib/openclaw /etc/ssh/sshd_config.d \
  /etc/fail2ban/jail.d /etc/sysctl.d /etc/systemd/journald.conf.d
chmod 0750 /etc/openclaw

rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  "${PROJECT_ROOT}/" "${INSTALL_ROOT}/"

cat >/usr/local/bin/openclaw-hostctl <<EOF
#!/usr/bin/env bash
set -euo pipefail
export OPENCLAW_HOST_ROOT="${INSTALL_ROOT}"
exec "${INSTALL_ROOT}/scripts/openclaw-hostctl" "\$@"
EOF
chmod 0755 /usr/local/bin/openclaw-hostctl
install -m 0755 "${INSTALL_ROOT}/bootstrap/openclaw-network-setup.sh" /usr/local/lib/openclaw/openclaw-network-setup.sh
install -m 0644 "${INSTALL_ROOT}/systemd/openclaw-vm@.service" /etc/systemd/system/openclaw-vm@.service
install -m 0644 "${INSTALL_ROOT}/bootstrap/openclaw-network.service" /etc/systemd/system/openclaw-network.service
install -m 0755 "${INSTALL_ROOT}/bootstrap/apply-lockdown.sh" /usr/local/lib/openclaw/apply-lockdown.sh
install -m 0644 "${INSTALL_ROOT}/bootstrap/sshd-hardening.conf" "${LOCKDOWN_CANDIDATE_DIR}/10-openclaw-hardening.conf"
install -m 0644 "${INSTALL_ROOT}/bootstrap/fail2ban-openclaw.local" "${LOCKDOWN_CANDIDATE_DIR}/openclaw-sshd.local"
install -m 0644 "${INSTALL_ROOT}/bootstrap/journald-openclaw.conf" /etc/systemd/journald.conf.d/openclaw.conf
install -m 0644 "${INSTALL_ROOT}/bootstrap/99-openclaw-host.conf" /etc/sysctl.d/99-openclaw-host.conf

if [[ ! -f "${CONFIG_DEST}" ]]; then
  install -m 0640 "${INSTALL_ROOT}/config/host-config.example.json" "${CONFIG_DEST}"
fi

python3 - "${CONFIG_DEST}" "${PUBLIC_IFACE}" "${ADMIN_CIDRS}" \
  "${INSTALL_ROOT}/bootstrap/nftables-openclaw.nft.tpl" "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf" <<'PY'
import json
import sys
from ipaddress import ip_interface
from pathlib import Path

config_path = Path(sys.argv[1])
public_iface = sys.argv[2]
admin_cidrs = sys.argv[3]
template_path = Path(sys.argv[4])
output_path = Path(sys.argv[5])

config = json.loads(config_path.read_text())
bridge = ip_interface(config["bridge_cidr"])
nat_rules = f'    oifname "{public_iface}" ip saddr {bridge.network} masquerade' if config["allow_guest_egress"] else ""

rendered = template_path.read_text()
rendered = rendered.replace("__PUBLIC_IFACE__", public_iface)
rendered = rendered.replace("__BRIDGE_NAME__", config["bridge_name"])
rendered = rendered.replace("__BRIDGE_NET__", str(bridge.network))
rendered = rendered.replace("__ADMIN_CIDRS__", ", ".join(item.strip() for item in admin_cidrs.split(",") if item.strip()))
rendered = rendered.replace("__NAT_RULES__", nat_rules)
output_path.write_text(rendered)
PY

chmod 0600 "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf"
nft -c -f "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf"
sysctl --system >/dev/null
systemctl daemon-reload
systemctl enable --now unattended-upgrades openclaw-network.service
systemctl restart systemd-journald

echo "host bootstrap complete"
echo "review ${CONFIG_DEST}, place Firecracker and base image artifacts, then run openclaw-hostctl validate-config"
echo "lockdown candidates were written to ${LOCKDOWN_CANDIDATE_DIR}"
echo "after confirming console access and admin CIDRs, apply hardening with: sudo /usr/local/lib/openclaw/apply-lockdown.sh"
