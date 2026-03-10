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
ALLOW_GUEST_EGRESS=$(read_config allow_guest_egress)

if [[ "$ACTION" == "up" ]]; then
  if ! ip link show "$BRIDGE_NAME" >/dev/null 2>&1; then
    ip link add "$BRIDGE_NAME" type bridge
  fi
  ip addr replace "$BRIDGE_CIDR" dev "$BRIDGE_NAME"
  ip link set "$BRIDGE_NAME" up
  if [[ "$ALLOW_GUEST_EGRESS" == "1" ]]; then
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
  fi
  exit 0
fi

if [[ "$ACTION" == "down" ]]; then
  ip link del "$BRIDGE_NAME" >/dev/null 2>&1 || true
  exit 0
fi

echo "unknown action: $ACTION" >&2
exit 2

