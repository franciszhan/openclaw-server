from __future__ import annotations

from dataclasses import asdict, dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_address, ip_interface
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HostConfig:
    storage_root: Path
    firecracker_bin: Path
    kernel_image: Path
    base_rootfs: Path
    storage_copy_mode: str
    bridge_name: str
    bridge_cidr: str
    first_guest_ip: str
    last_guest_ip: str
    guest_dns: list[str]
    vcpu_count: int
    mem_mib: int
    smt: bool
    allow_guest_egress: bool
    admin_ssh_keys_path: Path
    shared_skills_dir: Path | None
    loop_mount_base: Path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostConfig":
        return cls(
            storage_root=Path(data["storage_root"]),
            firecracker_bin=Path(data["firecracker_bin"]),
            kernel_image=Path(data["kernel_image"]),
            base_rootfs=Path(data["base_rootfs"]),
            storage_copy_mode=data.get("storage_copy_mode", "reflink"),
            bridge_name=data["bridge_name"],
            bridge_cidr=data["bridge_cidr"],
            first_guest_ip=data["first_guest_ip"],
            last_guest_ip=data["last_guest_ip"],
            guest_dns=list(data["guest_dns"]),
            vcpu_count=int(data["vcpu_count"]),
            mem_mib=int(data["mem_mib"]),
            smt=bool(data["smt"]),
            allow_guest_egress=bool(data["allow_guest_egress"]),
            admin_ssh_keys_path=Path(data["admin_ssh_keys_path"]),
            shared_skills_dir=Path(data["shared_skills_dir"]) if data.get("shared_skills_dir") else None,
            loop_mount_base=Path(data["loop_mount_base"]),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in data.items()
        }

    @property
    def bridge_interface(self) -> IPv4Interface:
        return ip_interface(self.bridge_cidr)

    @property
    def bridge_network(self) -> IPv4Network:
        return self.bridge_interface.network

    @property
    def bridge_host_ip(self) -> IPv4Address:
        return self.bridge_interface.ip

    @property
    def guest_ip_start(self) -> IPv4Address:
        return ip_address(self.first_guest_ip)

    @property
    def guest_ip_end(self) -> IPv4Address:
        return ip_address(self.last_guest_ip)

    @property
    def vm_root(self) -> Path:
        return self.storage_root / "vms"

    @property
    def base_dir(self) -> Path:
        return self.storage_root / "base"

    def user_dir(self, user_id: str) -> Path:
        return self.vm_root / user_id

    def user_record_path(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "vm.json"

    def user_rootfs_path(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "rootfs.ext4"

    def snapshot_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "snapshots"

    def runtime_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "runtime"

    def api_socket_path(self, user_id: str) -> Path:
        return self.runtime_dir(user_id) / "firecracker.socket"


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    display_name: str
    machine_name: str
    ip_address: str
    mac_address: str
    tap_name: str
    rootfs_path: str
    created_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserRecord":
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
