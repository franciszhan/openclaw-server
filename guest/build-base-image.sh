#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)

CODENAME=${CODENAME:-bookworm}
IMAGE_PATH=${IMAGE_PATH:-/var/lib/openclaw/base/openclaw-base.ext4}
IMAGE_SIZE_GIB=${IMAGE_SIZE_GIB:-12}
MOUNT_DIR=$(mktemp -d)
OPENCLAW_INSTALL_CMD=${OPENCLAW_INSTALL_CMD:-}
SHARED_SKILLS_DIR=${SHARED_SKILLS_DIR:-}

cleanup() {
  mountpoint -q "${MOUNT_DIR}/dev" && umount "${MOUNT_DIR}/dev" || true
  mountpoint -q "${MOUNT_DIR}/proc" && umount "${MOUNT_DIR}/proc" || true
  mountpoint -q "${MOUNT_DIR}/sys" && umount "${MOUNT_DIR}/sys" || true
  mountpoint -q "${MOUNT_DIR}" && umount "${MOUNT_DIR}" || true
  rm -rf "${MOUNT_DIR}"
}
trap cleanup EXIT

mkdir -p "$(dirname "${IMAGE_PATH}")"
truncate -s "${IMAGE_SIZE_GIB}G" "${IMAGE_PATH}"
mkfs.ext4 -F -L openclaw-root "${IMAGE_PATH}"
mount -o loop "${IMAGE_PATH}" "${MOUNT_DIR}"

debootstrap --variant=minbase "${CODENAME}" "${MOUNT_DIR}" http://deb.debian.org/debian
mount --bind /dev "${MOUNT_DIR}/dev"
mount -t proc proc "${MOUNT_DIR}/proc"
mount -t sysfs sysfs "${MOUNT_DIR}/sys"

chroot "${MOUNT_DIR}" apt-get update
DEBIAN_FRONTEND=noninteractive chroot "${MOUNT_DIR}" apt-get install -y \
  ca-certificates \
  curl \
  iproute2 \
  openssh-server \
  rsync \
  sudo \
  systemd \
  systemd-sysv

chroot "${MOUNT_DIR}" useradd --create-home --shell /bin/bash admin
printf 'admin ALL=(ALL) NOPASSWD:ALL\n' > "${MOUNT_DIR}/etc/sudoers.d/10-admin"
chmod 0440 "${MOUNT_DIR}/etc/sudoers.d/10-admin"

mkdir -p "${MOUNT_DIR}/etc/systemd/network" \
  "${MOUNT_DIR}/etc/openclaw" \
  "${MOUNT_DIR}/var/lib/openclaw/bootstrap" \
  "${MOUNT_DIR}/opt/openclaw/skills/company" \
  "${MOUNT_DIR}/usr/local/sbin"

install -m 0755 "${PROJECT_ROOT}/guest/openclaw-firstboot.sh" "${MOUNT_DIR}/usr/local/sbin/openclaw-firstboot.sh"
install -m 0644 "${PROJECT_ROOT}/guest/openclaw-firstboot.service" "${MOUNT_DIR}/etc/systemd/system/openclaw-firstboot.service"

if [[ -n "${SHARED_SKILLS_DIR}" && -d "${SHARED_SKILLS_DIR}" ]]; then
  rsync -a "${SHARED_SKILLS_DIR}/" "${MOUNT_DIR}/opt/openclaw/skills/company/"
fi

if [[ -n "${OPENCLAW_INSTALL_CMD}" ]]; then
  chroot "${MOUNT_DIR}" /bin/bash -lc "${OPENCLAW_INSTALL_CMD}"
fi

chroot "${MOUNT_DIR}" systemctl enable ssh systemd-networkd openclaw-firstboot.service systemd-resolved
ln -sf /run/systemd/resolve/stub-resolv.conf "${MOUNT_DIR}/etc/resolv.conf"

echo "openclaw-base" > "${MOUNT_DIR}/etc/hostname"

echo "base image ready at ${IMAGE_PATH}"

