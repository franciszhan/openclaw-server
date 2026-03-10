from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from ipaddress import IPv4Address, ip_address
from pathlib import Path

from .config import list_user_records, load_user_record, save_user_record
from .disk import (
    chown_tree,
    clone_disk,
    copy_tree,
    ensure_reflink_supported,
    extend_ext4_image,
    lookup_guest_user,
    mounted_image,
    run,
    sanitize_snapshot_label,
    write_file,
)
from .firecracker import make_mac_address, make_tap_name, write_firecracker_config
from .models import HostConfig, UserRecord


class HostController:
    def __init__(self, config: HostConfig) -> None:
        self.config = config

    def init_layout(self) -> None:
        for path in (
            self.config.storage_root,
            self.config.base_dir,
            self.config.vm_root,
            self.config.loop_mount_base,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        messages: list[str] = []
        if self.config.storage_copy_mode not in {"reflink", "copy"}:
            messages.append("storage_copy_mode must be one of: reflink, copy")
        if self.config.guest_ip_start not in self.config.bridge_network:
            messages.append("first_guest_ip is outside bridge_cidr")
        if self.config.guest_ip_end not in self.config.bridge_network:
            messages.append("last_guest_ip is outside bridge_cidr")
        if int(self.config.guest_ip_start) > int(self.config.guest_ip_end):
            messages.append("first_guest_ip must be <= last_guest_ip")
        if self.config.storage_copy_mode == "reflink" and self.config.storage_root.exists():
            try:
                ensure_reflink_supported(self.config.storage_root)
            except subprocess.CalledProcessError:
                messages.append(
                    "storage_root does not support reflink copies; use XFS with reflink or btrfs"
                )
        return messages

    def list_users(self) -> list[UserRecord]:
        return list_user_records(self.config.vm_root)

    def provision_user(
        self,
        user_id: str,
        *,
        display_name: str | None,
        user_config_path: Path | None,
        disk_size_gib: int | None,
    ) -> UserRecord:
        require_root()
        self.init_layout()
        self._assert_prerequisites()
        user_dir = self.config.user_dir(user_id)
        if user_dir.exists():
            raise ValueError(f"user '{user_id}' already exists")

        ip_address_text = str(self._allocate_guest_ip())
        machine_name = f"openclaw-{user_id}"
        tap_name = make_tap_name(user_id)
        record = UserRecord(
            user_id=user_id,
            display_name=display_name or user_id,
            machine_name=machine_name,
            ip_address=ip_address_text,
            mac_address=make_mac_address(ip_address_text),
            tap_name=tap_name,
            rootfs_path=str(self.config.user_rootfs_path(user_id)),
            created_at=timestamp_now(),
        )

        user_dir.mkdir(parents=True)
        self.config.snapshot_dir(user_id).mkdir(parents=True)
        self.config.runtime_dir(user_id).mkdir(parents=True)

        clone_disk(self.config.base_rootfs, Path(record.rootfs_path), mode=self.config.storage_copy_mode)
        if disk_size_gib:
            extend_ext4_image(Path(record.rootfs_path), disk_size_gib)

        with mounted_image(Path(record.rootfs_path), self.config.loop_mount_base, user_id) as mount_dir:
            self._seed_guest_files(mount_dir, record, user_config_path)

        save_user_record(self.config.user_record_path(user_id), record)
        self.create_snapshot(user_id, "initial")
        return record

    def create_snapshot(self, user_id: str, label: str) -> Path:
        require_root()
        user = self._load_user(user_id)
        self._assert_stopped(user_id)
        snapshot_label = sanitize_snapshot_label(label)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot_name = f"{timestamp}-{snapshot_label}"
        snapshot_root = self.config.snapshot_dir(user_id) / snapshot_name
        snapshot_root.mkdir(parents=True, exist_ok=False)
        snapshot_path = snapshot_root / "rootfs.ext4"
        clone_disk(Path(user.rootfs_path), snapshot_path, mode=self.config.storage_copy_mode)
        metadata = {
            "created_at": timestamp_now(),
            "label": snapshot_label,
            "user_id": user_id,
            "source_rootfs": user.rootfs_path,
        }
        (snapshot_root / "snapshot.json").write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        return snapshot_root

    def list_snapshots(self, user_id: str) -> list[Path]:
        snapshot_dir = self.config.snapshot_dir(user_id)
        if not snapshot_dir.exists():
            return []
        return sorted(path for path in snapshot_dir.iterdir() if path.is_dir())

    def restore_snapshot(self, user_id: str, snapshot_name: str, *, force: bool) -> Path:
        require_root()
        user = self._load_user(user_id)
        if self.is_running(user_id):
            if not force:
                raise ValueError("restore requires the microVM to be stopped; pass --force to stop it")
            self.stop_user(user_id)
        snapshot_root = self.config.snapshot_dir(user_id) / snapshot_name
        snapshot_rootfs = snapshot_root / "rootfs.ext4"
        if not snapshot_rootfs.exists():
            raise ValueError(f"snapshot '{snapshot_name}' does not exist")
        self.create_snapshot(user_id, "pre-restore")
        active_rootfs = Path(user.rootfs_path)
        active_rootfs.unlink()
        clone_disk(snapshot_rootfs, active_rootfs, mode=self.config.storage_copy_mode)
        return active_rootfs

    def start_user(self, user_id: str) -> None:
        require_root()
        self._load_user(user_id)
        run(["systemctl", "start", f"openclaw-vm@{user_id}.service"])

    def stop_user(self, user_id: str) -> None:
        require_root()
        self._load_user(user_id)
        run(["systemctl", "stop", f"openclaw-vm@{user_id}.service"])

    def status(self, user_id: str | None = None) -> list[dict[str, object]]:
        users = [self._load_user(user_id)] if user_id else self.list_users()
        status_rows: list[dict[str, object]] = []
        for user in users:
            service = f"openclaw-vm@{user.user_id}.service"
            active = subprocess.run(
                ["systemctl", "is-active", service],
                check=False,
                text=True,
                capture_output=True,
            )
            status_rows.append(
                {
                    "user_id": user.user_id,
                    "display_name": user.display_name,
                    "ip_address": user.ip_address,
                    "service": active.stdout.strip() or "inactive",
                    "snapshots": len(self.list_snapshots(user.user_id)),
                }
            )
        return status_rows

    def runtime_prepare(self, user_id: str) -> None:
        require_root()
        user = self._load_user(user_id)
        runtime_dir = self.config.runtime_dir(user_id)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_tap(user.tap_name)
        write_firecracker_config(runtime_dir / "firecracker.json", self.config, user)

    def runtime_cleanup(self, user_id: str) -> None:
        require_root()
        user = self._load_user(user_id)
        subprocess.run(["ip", "link", "del", user.tap_name], check=False, text=True)

    def is_running(self, user_id: str) -> bool:
        service = f"openclaw-vm@{user_id}.service"
        result = subprocess.run(
            ["systemctl", "is-active", service],
            check=False,
            text=True,
            capture_output=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "active"

    def _assert_prerequisites(self) -> None:
        missing = []
        for path in (self.config.base_rootfs, self.config.kernel_image, self.config.admin_ssh_keys_path):
            if not path.exists():
                missing.append(str(path))
        if missing:
            joined = ", ".join(missing)
            raise FileNotFoundError(f"missing required files: {joined}")
        if self.config.storage_copy_mode == "reflink":
            ensure_reflink_supported(self.config.storage_root)

    def _load_user(self, user_id: str) -> UserRecord:
        path = self.config.user_record_path(user_id)
        if not path.exists():
            raise ValueError(f"user '{user_id}' does not exist")
        return load_user_record(path)

    def _allocate_guest_ip(self) -> IPv4Address:
        used = {ip_address(record.ip_address) for record in self.list_users()}
        current = self.config.guest_ip_start
        while int(current) <= int(self.config.guest_ip_end):
            if current not in used:
                return current
            current = IPv4Address(int(current) + 1)
        raise RuntimeError("guest IP pool is exhausted")

    def _seed_guest_files(self, mount_dir: Path, user: UserRecord, user_config_path: Path | None) -> None:
        write_file(mount_dir, "/etc/hostname", user.machine_name + "\n", 0o644)
        network_config = render_guest_network(self.config, user.ip_address)
        write_file(mount_dir, "/etc/systemd/network/20-eth0.network", network_config, 0o644)
        write_file(
            mount_dir,
            "/var/lib/openclaw/bootstrap/employee.env",
            f"OPENCLAW_USER_ID={user.user_id}\nOPENCLAW_DISPLAY_NAME={user.display_name}\n",
            0o640,
        )

        admin_ssh_dir = mount_dir / "home/admin/.ssh"
        admin_ssh_dir.mkdir(parents=True, exist_ok=True)
        authorized_keys = admin_ssh_dir / "authorized_keys"
        shutil.copyfile(self.config.admin_ssh_keys_path, authorized_keys)
        os.chmod(authorized_keys, 0o600)
        uid, gid = lookup_guest_user(mount_dir, "admin")
        chown_tree(admin_ssh_dir, uid, gid)

        if user_config_path:
            write_path = mount_dir / "etc/openclaw"
            write_path.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(user_config_path, write_path / "config.json")
            os.chmod(write_path / "config.json", 0o640)

        if self.config.shared_skills_dir and self.config.shared_skills_dir.exists():
            destination = mount_dir / "opt/openclaw/skills/company"
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_tree(self.config.shared_skills_dir, destination)

    def _assert_stopped(self, user_id: str) -> None:
        if self.is_running(user_id):
            raise ValueError("operation requires the microVM to be stopped")

    def _ensure_tap(self, tap_name: str) -> None:
        result = subprocess.run(
            ["ip", "link", "show", tap_name],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            run(["bridge", "link", "set", "dev", tap_name, "isolated", "on"])
            run(["ip", "link", "set", tap_name, "up"])
            return
        run(["ip", "tuntap", "add", "dev", tap_name, "mode", "tap"])
        run(["ip", "link", "set", tap_name, "master", self.config.bridge_name])
        run(["bridge", "link", "set", "dev", tap_name, "isolated", "on"])
        run(["ip", "link", "set", tap_name, "up"])


def render_guest_network(config: HostConfig, guest_ip: str) -> str:
    dns_lines = "\n".join(f"DNS={server}" for server in config.guest_dns)
    return (
        "[Match]\n"
        "Name=eth0\n\n"
        "[Network]\n"
        f"Address={guest_ip}/{config.bridge_network.prefixlen}\n"
        f"Gateway={config.bridge_host_ip}\n"
        f"{dns_lines}\n"
        "IPv6AcceptRA=no\n"
    )


def timestamp_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this command must run as root")
