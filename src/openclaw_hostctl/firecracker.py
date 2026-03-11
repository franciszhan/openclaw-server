from __future__ import annotations

import hashlib
import json
from ipaddress import ip_address
from pathlib import Path

from .models import HostConfig, UserRecord


def make_tap_name(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:10]
    return f"oc{digest[:13]}"


def make_mac_address(ip: str) -> str:
    octets = [int(part) for part in ip.split(".")]
    return "06:00:%02x:%02x:%02x:%02x" % tuple(octets)


def render_firecracker_config(config: HostConfig, user: UserRecord) -> dict[str, object]:
    return {
        "boot-source": {
            "kernel_image_path": str(config.kernel_image),
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off quiet root=/dev/vda rw rootfstype=ext4",
        },
        "drives": [
            {
                "drive_id": "rootfs",
                "path_on_host": user.rootfs_path,
                "is_root_device": True,
                "is_read_only": False,
            }
        ],
        "machine-config": {
            "vcpu_count": config.vcpu_count,
            "mem_size_mib": config.mem_mib,
            "smt": config.smt,
        },
        "network-interfaces": [
            {
                "iface_id": "eth0",
                "host_dev_name": user.tap_name,
                "guest_mac": user.mac_address,
            }
        ],
    }


def write_firecracker_config(path: Path, config: HostConfig, user: UserRecord) -> None:
    path.write_text(
        json.dumps(render_firecracker_config(config, user), indent=2) + "\n",
        encoding="utf-8",
    )
