#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=/var/lib/openclaw/bootstrap
STAMP_FILE=/var/lib/openclaw/.firstboot-complete

if [[ -f "$STAMP_FILE" ]]; then
  exit 0
fi

mkdir -p /var/lib/openclaw/runtime /opt/openclaw/skills/company

if [[ -f "${STATE_DIR}/employee.env" ]]; then
  cp "${STATE_DIR}/employee.env" /var/lib/openclaw/runtime/employee.env
  chmod 0640 /var/lib/openclaw/runtime/employee.env
fi

if [[ -f /etc/openclaw/config.json ]]; then
  chmod 0640 /etc/openclaw/config.json
fi

touch "$STAMP_FILE"

