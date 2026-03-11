#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

LOCKDOWN_CANDIDATE_DIR=${LOCKDOWN_CANDIDATE_DIR:-/etc/openclaw/lockdown}

if [[ ! -f "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf" ]]; then
  echo "missing ${LOCKDOWN_CANDIDATE_DIR}/nftables.conf" >&2
  exit 1
fi

nft -c -f "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf"

install -m 0600 "${LOCKDOWN_CANDIDATE_DIR}/nftables.conf" /etc/nftables.conf
install -m 0644 "${LOCKDOWN_CANDIDATE_DIR}/10-openclaw-hardening.conf" /etc/ssh/sshd_config.d/10-openclaw-hardening.conf
install -m 0644 "${LOCKDOWN_CANDIDATE_DIR}/openclaw-sshd.local" /etc/fail2ban/jail.d/openclaw-sshd.local

systemctl enable --now nftables fail2ban
systemctl restart ssh

echo "lockdown applied"
echo "verify SSH from a second terminal before closing your console session"
