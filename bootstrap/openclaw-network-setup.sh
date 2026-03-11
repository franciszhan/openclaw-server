#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH=${OPENCLAW_HOST_CONFIG:-/etc/openclaw/host-config.json}
ACTION=${1:-up}

read_config() {
  python3 - "$CONFIG_PATH" "$1" <<'PY'
import json
import sys
from ipaddress import ip_interface
from pathlib import Path

config_path = Path(sys.argv[1])
field = sys.argv[2]
data = json.loads(config_path.read_text())
if field == "bridge_name":
    print(data["bridge_name"])
elif field == "bridge_cidr":
    print(data["bridge_cidr"])
elif field == "bridge_net":
    print(ip_interface(data["bridge_cidr"]).network)
elif field == "bridge_host_ip":
    print(ip_interface(data["bridge_cidr"]).ip)
elif field == "allow_guest_egress":
    print("1" if data["allow_guest_egress"] else "0")
else:
    raise SystemExit(f"unknown field {field}")
PY
}

BRIDGE_NAME=$(read_config bridge_name)
BRIDGE_CIDR=$(read_config bridge_cidr)
BRIDGE_NET=$(read_config bridge_net)
ALLOW_GUEST_EGRESS=$(read_config allow_guest_egress)
PUBLIC_IFACE=${PUBLIC_IFACE:-$(ip route show default | awk '/default/ {print $5; exit}')}
RUNTIME_NAT_TABLE=openclaw_runtime_nat

cleanup_runtime_nat() {
  nft delete table ip "${RUNTIME_NAT_TABLE}" >/dev/null 2>&1 || true
}

ensure_runtime_nat() {
  cleanup_runtime_nat
  nft add table ip "${RUNTIME_NAT_TABLE}"
  nft "add chain ip ${RUNTIME_NAT_TABLE} postrouting { type nat hook postrouting priority srcnat; }"
  nft add rule ip "${RUNTIME_NAT_TABLE}" postrouting \
    oifname "${PUBLIC_IFACE}" ip saddr "${BRIDGE_NET}" masquerade
}

if [[ "$ACTION" == "up" ]]; then
  if ! ip link show "$BRIDGE_NAME" >/dev/null 2>&1; then
    ip link add "$BRIDGE_NAME" type bridge
  fi
  ip addr replace "$BRIDGE_CIDR" dev "$BRIDGE_NAME"
  ip link set "$BRIDGE_NAME" up
  if [[ "$ALLOW_GUEST_EGRESS" == "1" ]]; then
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    ensure_runtime_nat
  else
    cleanup_runtime_nat
  fi
  exit 0
fi

if [[ "$ACTION" == "down" ]]; then
  cleanup_runtime_nat
  ip link del "$BRIDGE_NAME" >/dev/null 2>&1 || true
  exit 0
fi

echo "unknown action: $ACTION" >&2
exit 2
