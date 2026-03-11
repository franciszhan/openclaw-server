#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

ENV_FILE=${ENV_FILE:-/etc/openclaw/render-lockdown.env}
CONFIG_DEST=${CONFIG_DEST:-/etc/openclaw/host-config.json}
LOCKDOWN_CANDIDATE_DIR=${LOCKDOWN_CANDIDATE_DIR:-/etc/openclaw/lockdown}
PUBLIC_IFACE=${PUBLIC_IFACE:-}
ADMIN_CIDRS=${ADMIN_CIDRS:-}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TEMPLATE_PATH=${TEMPLATE_PATH:-"${SCRIPT_DIR}/nftables-openclaw.nft.tpl"}

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

PUBLIC_IFACE=${PUBLIC_IFACE:-$(ip route show default | awk '/default/ {print $5; exit}')}

if [[ -z "${ADMIN_CIDRS}" ]]; then
  echo "set ADMIN_CIDRS to a comma-separated list of allowed admin networks" >&2
  exit 1
fi

mkdir -p "${LOCKDOWN_CANDIDATE_DIR}"

python3 - "${CONFIG_DEST}" "${PUBLIC_IFACE}" "${ADMIN_CIDRS}" \
  "${TEMPLATE_PATH}" "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf" <<'PY'
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

echo "rendered lockdown candidates in ${LOCKDOWN_CANDIDATE_DIR}"
