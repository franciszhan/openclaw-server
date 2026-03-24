from __future__ import annotations

import json
import os
import secrets
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

DEFAULT_COORDINATOR_CONFIG_PATH = Path("/etc/openclaw/coordinator-config.json")
DEFAULT_GOOGLE_OAUTH_CLIENT_PATH = Path("/etc/openclaw/google-oauth-client.json")


class HostController:
    def __init__(self, config: HostConfig) -> None:
        self.config = config

    def init_layout(self) -> None:
        for path in (
            self.config.storage_root,
            self.config.base_dir,
            self.config.vm_root,
            self.config.loop_mount_base,
            self.config.shared_access_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        messages: list[str] = []
        if self.config.storage_copy_mode not in {"reflink", "copy"}:
            messages.append("storage_copy_mode must be one of: reflink, copy")
        if bool(self.config.automation_ssh_private_key_path) != bool(
            self.config.automation_ssh_public_key_path
        ):
            messages.append(
                "automation_ssh_private_key_path and automation_ssh_public_key_path must be set together"
            )
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

        stored_user_config_path = self._store_optional_file(
            user_config_path,
            self.config.user_config_store_path(user_id),
        )
        if stored_user_config_path:
            self._sync_coordinator_directory_from_manifest(
                record,
                load_json_file(stored_user_config_path),
            )
        with mounted_image(Path(record.rootfs_path), self.config.loop_mount_base, user_id) as mount_dir:
            self._seed_guest_files(mount_dir, record, stored_user_config_path)

        save_user_record(self.config.user_record_path(user_id), record)
        self.create_snapshot(user_id, "initial")
        return record

    def activate_user(
        self,
        user_id: str,
        *,
        manifest_path: Path | None,
        user_config_path: Path | None,
        activation_config_path: Path | None,
        secrets_env_path: Path | None,
        force: bool,
        restart: bool,
    ) -> dict[str, object]:
        require_root()
        user = self._load_user(user_id)
        was_running = self.is_running(user_id)
        if was_running:
            if not force:
                raise ValueError("activation requires the microVM to be stopped; pass --force to stop it")
            self.stop_user(user_id)

        stored_manifest = self._resolve_stored_input(
            manifest_path,
            self.config.user_config_store_path(user_id),
        )
        stored_activation_config = self._resolve_stored_input(
            activation_config_path,
            self.config.activation_config_store_path(user_id),
        )
        stored_secrets_env = self._resolve_stored_input(
            secrets_env_path,
            self.config.activation_secrets_store_path(user_id),
        )

        with mounted_image(Path(user.rootfs_path), self.config.loop_mount_base, user_id) as mount_dir:
            if stored_manifest:
                manifest = load_json_file(stored_manifest)
                self._apply_manifest_files(mount_dir, manifest)
                self._sync_coordinator_directory_from_manifest(user, manifest)
            else:
                stored_user_config = self._resolve_stored_input(
                    user_config_path,
                    self.config.user_config_store_path(user_id),
                )
                self._apply_activation_files(
                    mount_dir,
                    stored_user_config,
                    stored_activation_config,
                    stored_secrets_env,
                )

        restarted = False
        if restart or was_running:
            self.start_user(user_id)
            restarted = True

        return {
            "user_id": user_id,
            "applied_manifest": str(stored_manifest) if stored_manifest else None,
            "applied_activation_config": (
                str(stored_activation_config) if stored_activation_config else None
            ),
            "applied_secrets_env": str(stored_secrets_env) if stored_secrets_env else None,
            "restarted": restarted,
        }

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
        self.config.api_socket_path(user_id).unlink(missing_ok=True)
        self._ensure_tap(user.tap_name)
        write_firecracker_config(runtime_dir / "firecracker.json", self.config, user)

    def runtime_cleanup(self, user_id: str) -> None:
        require_root()
        user = self._load_user(user_id)
        self.config.api_socket_path(user_id).unlink(missing_ok=True)
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
        network_config = render_guest_network(self.config, user.ip_address, user.mac_address)
        write_file(mount_dir, "/etc/systemd/network/20-eth0.network", network_config, 0o644)
        write_file(
            mount_dir,
            "/var/lib/openclaw/bootstrap/employee.env",
            f"OPENCLAW_USER_ID={user.user_id}\nOPENCLAW_DISPLAY_NAME={user.display_name}\n",
            0o640,
        )

        if user_config_path:
            manifest_data = load_json_file(user_config_path)
            guest_data = build_guest_user_config(manifest_data)
            write_path = mount_dir / "etc/openclaw"
            write_path.mkdir(parents=True, exist_ok=True)
            (write_path / "config.json").write_text(
                json.dumps(guest_data, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(write_path / "config.json", 0o640)

        if self.config.shared_skills_dir and self.config.shared_skills_dir.exists():
            destination = mount_dir / "opt/openclaw/skills/company"
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_tree(self.config.shared_skills_dir, destination)
        google_state_dir = ensure_guest_google_oauth_runtime(mount_dir)
        self._ensure_guest_admin_ssh(mount_dir)
        if google_state_dir:
            uid, gid = lookup_guest_user(mount_dir, "admin")
            chown_tree(google_state_dir, uid, gid)

    def _apply_activation_files(
        self,
        mount_dir: Path,
        user_config_path: Path | None,
        activation_config_path: Path | None,
        secrets_env_path: Path | None,
    ) -> None:
        config_dir = mount_dir / "etc/openclaw"
        config_dir.mkdir(parents=True, exist_ok=True)

        if user_config_path:
            destination = config_dir / "config.json"
            shutil.copyfile(user_config_path, destination)
            os.chmod(destination, 0o640)

        if activation_config_path:
            destination = config_dir / "activation.json"
            shutil.copyfile(activation_config_path, destination)
            os.chmod(destination, 0o640)

        if secrets_env_path:
            destination = config_dir / "secrets.env"
            shutil.copyfile(secrets_env_path, destination)
            os.chmod(destination, 0o600)

    def _apply_manifest_files(self, mount_dir: Path, manifest: dict[str, object]) -> None:
        guest_config = build_guest_user_config(manifest)
        guest_config_dir = mount_dir / "etc/openclaw"
        guest_config_dir.mkdir(parents=True, exist_ok=True)
        (guest_config_dir / "config.json").write_text(
            json.dumps(guest_config, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(guest_config_dir / "config.json", 0o640)

        openclaw_state_dir = mount_dir / "home/admin/.openclaw"
        openclaw_state_dir.mkdir(parents=True, exist_ok=True)
        credentials_dir = openclaw_state_dir / "credentials"
        credentials_dir.mkdir(parents=True, exist_ok=True)
        auth_state_dir = openclaw_state_dir / "agents/main/agent"
        auth_state_dir.mkdir(parents=True, exist_ok=True)
        systemd_user_dir = mount_dir / "home/admin/.config/systemd/user"
        systemd_user_dir.mkdir(parents=True, exist_ok=True)
        wants_dir = systemd_user_dir / "default.target.wants"
        wants_dir.mkdir(parents=True, exist_ok=True)
        linger_dir = mount_dir / "var/lib/systemd/linger"
        linger_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_state_dir = mount_dir / "var/lib/openclaw/bootstrap"
        bootstrap_state_dir.mkdir(parents=True, exist_ok=True)

        env_values = extract_openclaw_env(manifest)
        if env_values:
            write_env_file(openclaw_state_dir / ".env", env_values)
            os.chmod(openclaw_state_dir / ".env", 0o600)

        openclaw_config = load_json_if_exists(openclaw_state_dir / "openclaw.json", default={})
        apply_openclaw_config_manifest(openclaw_config, manifest)
        (openclaw_state_dir / "openclaw.json").write_text(
            json.dumps(openclaw_config, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(openclaw_state_dir / "openclaw.json", 0o600)

        auth_profiles = load_json_if_exists(
            auth_state_dir / "auth-profiles.json",
            default={"version": 1, "profiles": {}, "usageStats": {}},
        )
        apply_openclaw_auth_profiles(auth_profiles, manifest)
        (auth_state_dir / "auth-profiles.json").write_text(
            json.dumps(auth_profiles, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(auth_state_dir / "auth-profiles.json", 0o600)

        slack_allow_from = extract_slack_allow_from(manifest)
        if slack_allow_from is not None:
            slack_allow_from_path = credentials_dir / "slack-default-allowFrom.json"
            slack_allow_from_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "allowFrom": slack_allow_from,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(slack_allow_from_path, 0o600)

        uid, gid = lookup_guest_user(mount_dir, "admin")
        os.chmod(openclaw_state_dir, 0o700)
        os.chmod(credentials_dir, 0o700)
        ensure_gateway_user_service(
            mount_dir,
            systemd_user_dir,
            wants_dir,
            openclaw_config,
        )
        ensure_gateway_activation_service(mount_dir, bootstrap_state_dir)
        if self.config.shared_skills_dir and self.config.shared_skills_dir.exists():
            destination = mount_dir / "opt/openclaw/skills/company"
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_tree(self.config.shared_skills_dir, destination)
        ensure_guest_package_runtime(mount_dir)
        ensure_shared_access_guest_runtime(mount_dir, manifest)
        ensure_guest_google_oauth_runtime(mount_dir)
        self._ensure_guest_admin_ssh(mount_dir)
        chown_tree(openclaw_state_dir, uid, gid)
        for path in (
            mount_dir / "home/admin/.config",
            mount_dir / "home/admin/.config/systemd",
            systemd_user_dir,
            wants_dir,
            systemd_user_dir / "openclaw-gateway.service",
            mount_dir / "usr/local/bin/openclaw-shared-access",
            mount_dir / "usr/local/bin/company-email-intro-lookup",
            mount_dir / "usr/local/bin/company-email-intro-draft",
        ):
            if path.exists():
                os.chown(path, uid, gid)

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

    def _store_optional_file(self, source_path: Path | None, destination_path: Path) -> Path | None:
        if not source_path:
            return None
        if source_path.resolve() == destination_path.resolve():
            return destination_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        return destination_path

    def _resolve_stored_input(self, source_path: Path | None, destination_path: Path) -> Path | None:
        stored_path = self._store_optional_file(source_path, destination_path)
        if stored_path:
            return stored_path
        if destination_path.exists():
            return destination_path
        return None

    def _ensure_guest_admin_ssh(self, mount_dir: Path) -> None:
        admin_ssh_dir = mount_dir / "home/admin/.ssh"
        admin_ssh_dir.mkdir(parents=True, exist_ok=True)
        authorized_keys = admin_ssh_dir / "authorized_keys"
        authorized_keys.write_text(self._render_guest_authorized_keys(), encoding="utf-8")
        os.chmod(authorized_keys, 0o600)
        uid, gid = lookup_guest_user(mount_dir, "admin")
        chown_tree(admin_ssh_dir, uid, gid)

    def _render_guest_authorized_keys(self) -> str:
        keys: list[str] = []
        for path in (
            self.config.admin_ssh_keys_path,
            self.config.automation_ssh_public_key_path,
        ):
            if not path:
                continue
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped and stripped not in keys:
                        keys.append(stripped)
        return "\n".join(keys) + ("\n" if keys else "")

    def execute_shared_access(
        self,
        user_id: str,
        request: dict[str, object],
        *,
        timeout_seconds: int = 60,
    ) -> dict[str, object]:
        require_root()
        user = self._load_user(user_id)
        private_key = self.config.automation_ssh_private_key_path
        if not private_key or not private_key.exists():
            raise FileNotFoundError("automation SSH private key is not configured")
        self.config.shared_access_root.mkdir(parents=True, exist_ok=True)
        self.config.shared_access_known_hosts_path.touch(exist_ok=True)
        command = [
            "ssh",
            "-i",
            str(private_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={self.config.shared_access_known_hosts_path}",
            "-o",
            "ConnectTimeout=10",
            f"admin@{user.ip_address}",
            "/usr/local/bin/openclaw-shared-access",
            "execute",
        ]
        result = subprocess.run(
            command,
            check=True,
            text=True,
            input=json.dumps(request),
            capture_output=True,
            timeout=timeout_seconds,
        )
        return json.loads(result.stdout)

    def _sync_coordinator_directory_from_manifest(
        self,
        user: UserRecord,
        manifest: dict[str, object],
        *,
        coordinator_config_path: Path = DEFAULT_COORDINATOR_CONFIG_PATH,
    ) -> None:
        slack_user_id = manifest.get("slack_user_id")
        if not isinstance(slack_user_id, str) or not slack_user_id.strip():
            return
        if not coordinator_config_path.exists():
            return
        shared_access = manifest.get("shared_access", {})
        if not isinstance(shared_access, dict):
            shared_access = {}
        try:
            from openclaw_coordinator.config import load_coordinator_config
            from openclaw_coordinator.models import DirectoryEntry
            from openclaw_coordinator.store import CoordinatorStore
        except ImportError:
            return
        coordinator_config = load_coordinator_config(coordinator_config_path)
        store = CoordinatorStore(coordinator_config.state_root)
        store.init_layout()
        store.upsert_directory_entry(
            DirectoryEntry(
                slack_user_id=slack_user_id.strip(),
                vm_user_id=user.user_id,
                display_name=user.display_name,
                vm_address=user.ip_address,
                opt_in=bool(shared_access.get("opt_in", False)),
                shared_capabilities=[
                    str(value) for value in shared_access.get("capabilities", [])
                ],
            )
        )


def render_guest_network(config: HostConfig, guest_ip: str, guest_mac: str) -> str:
    dns_lines = "\n".join(f"DNS={server}" for server in config.guest_dns)
    return (
        "[Match]\n"
        f"MACAddress={guest_mac}\n\n"
        "[Network]\n"
        f"Address={guest_ip}/{config.bridge_network.prefixlen}\n"
        f"Gateway={config.bridge_host_ip}\n"
        f"{dns_lines}\n"
        "IPv6AcceptRA=no\n"
    )


def load_json_file(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_if_exists(path: Path, *, default: dict[str, object]) -> dict[str, object]:
    if not path.exists():
        return json.loads(json.dumps(default))
    return load_json_file(path)


def build_guest_user_config(manifest: dict[str, object]) -> dict[str, object]:
    stripped_keys = {"openclaw", "shared_access", "slack_user_id"}
    return {key: value for key, value in manifest.items() if key not in stripped_keys}


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_openclaw_env(manifest: dict[str, object]) -> dict[str, str]:
    openclaw = manifest.get("openclaw", {})
    if not isinstance(openclaw, dict):
        return {}
    env_values = openclaw.get("env", {})
    if not isinstance(env_values, dict):
        return {}
    return {
        key: str(value)
        for key, value in env_values.items()
    }


def extract_slack_allow_from(manifest: dict[str, object]) -> list[str] | None:
    slack = _extract_slack_manifest(manifest)
    allow_from = slack.get("allowFrom")
    if allow_from is None:
        return None
    if not isinstance(allow_from, list):
        raise ValueError("openclaw.channels.slack.allowFrom must be a list")
    return [str(value) for value in allow_from]


def extract_shared_access_config(manifest: dict[str, object]) -> dict[str, object]:
    shared_access = manifest.get("shared_access", {})
    if not isinstance(shared_access, dict):
        return {}
    config = json.loads(json.dumps(shared_access))
    slack_user_id = manifest.get("slack_user_id")
    if slack_user_id and isinstance(slack_user_id, str):
        config.setdefault("slack_user_id", slack_user_id)
    config.setdefault(
        "capabilities",
        ["email_intro_lookup", "draft_intro_from_email_context"],
    )
    lookup = config.setdefault("email_intro_lookup", {})
    if isinstance(lookup, dict):
        lookup.setdefault(
            "allowedRecipientFilters",
            [
                "leads@tribecap.co",
                "portfolio-passive@tribecap.co",
                "portfolio-active@tribecap.co",
                "crypto-passive@tribecap.co",
                "crypto@tribecap.co",
            ],
        )
        lookup.setdefault(
            "command",
            [
                "/usr/local/bin/company-email-intro-lookup",
                "--request",
                "{request_path}",
                "--response",
                "{response_path}",
            ],
        )
    draft = config.setdefault("draft_intro_from_email_context", {})
    if isinstance(draft, dict):
        draft.setdefault(
            "command",
            [
                "/usr/local/bin/company-email-intro-draft",
                "--request",
                "{request_path}",
                "--response",
                "{response_path}",
            ],
        )
    return config


def apply_openclaw_config_manifest(config: dict[str, object], manifest: dict[str, object]) -> None:
    openclaw = manifest.get("openclaw", {})
    if not isinstance(openclaw, dict):
        return

    agents_section = config.setdefault("agents", {})
    if not isinstance(agents_section, dict):
        raise ValueError("openclaw.json agents section must be a JSON object")
    agent_defaults = agents_section.setdefault("defaults", {})
    if not isinstance(agent_defaults, dict):
        raise ValueError("openclaw.json agents.defaults must be a JSON object")
    desired_model = str(openclaw.get("defaultModel", "openai/gpt-5.4")).strip() or "openai/gpt-5.4"
    model_section = agent_defaults.setdefault("model", {})
    if not isinstance(model_section, dict):
        raise ValueError("openclaw.json agents.defaults.model must be a JSON object")
    model_section["primary"] = desired_model
    models_section = agent_defaults.setdefault("models", {})
    if not isinstance(models_section, dict):
        raise ValueError("openclaw.json agents.defaults.models must be a JSON object")
    current_model_config = models_section.get(desired_model, {})
    if current_model_config and not isinstance(current_model_config, dict):
        raise ValueError("openclaw.json agents.defaults.models entries must be JSON objects")
    model_config = dict(current_model_config) if isinstance(current_model_config, dict) else {}
    model_config.setdefault("alias", "GPT")
    models_section[desired_model] = model_config
    agent_defaults.setdefault("workspace", "/home/admin/.openclaw/workspace")
    tools_section = config.setdefault("tools", {})
    if not isinstance(tools_section, dict):
        raise ValueError("openclaw.json tools section must be a JSON object")
    tools_section.setdefault("profile", "coding")
    commands_section = config.setdefault("commands", {})
    if not isinstance(commands_section, dict):
        raise ValueError("openclaw.json commands section must be a JSON object")
    commands_section.setdefault("native", "auto")
    commands_section.setdefault("nativeSkills", "auto")
    commands_section.setdefault("restart", True)
    commands_section.setdefault("ownerDisplay", "raw")
    session_section = config.setdefault("session", {})
    if not isinstance(session_section, dict):
        raise ValueError("openclaw.json session section must be a JSON object")
    session_section.setdefault("dmScope", "per-channel-peer")
    plugins_section = config.setdefault("plugins", {})
    if not isinstance(plugins_section, dict):
        raise ValueError("openclaw.json plugins section must be a JSON object")
    plugin_entries = plugins_section.setdefault("entries", {})
    if not isinstance(plugin_entries, dict):
        raise ValueError("openclaw.json plugins.entries must be a JSON object")
    slack_plugin = plugin_entries.setdefault("slack", {})
    if not isinstance(slack_plugin, dict):
        raise ValueError("openclaw.json plugins.entries.slack must be a JSON object")
    slack_plugin.setdefault("enabled", True)
    wizard_section = config.setdefault("wizard", {})
    if not isinstance(wizard_section, dict):
        raise ValueError("openclaw.json wizard section must be a JSON object")
    wizard_section.setdefault("lastRunCommand", "onboard")
    wizard_section.setdefault("lastRunMode", "local")

    gateway_section = config.setdefault("gateway", {})
    if not isinstance(gateway_section, dict):
        raise ValueError("openclaw.json gateway section must be a JSON object")
    gateway_section.setdefault("port", 18789)
    gateway_section.setdefault("mode", "local")
    gateway_section.setdefault("bind", "loopback")
    gateway_auth = gateway_section.setdefault("auth", {})
    if not isinstance(gateway_auth, dict):
        raise ValueError("openclaw.json gateway.auth section must be a JSON object")
    gateway_auth.setdefault("mode", "token")
    gateway_auth.setdefault("token", secrets.token_hex(24))
    gateway_tailscale = gateway_section.setdefault("tailscale", {})
    if not isinstance(gateway_tailscale, dict):
        raise ValueError("openclaw.json gateway.tailscale section must be a JSON object")
    gateway_tailscale.setdefault("mode", "off")
    gateway_tailscale.setdefault("resetOnExit", False)
    gateway_nodes = gateway_section.setdefault("nodes", {})
    if not isinstance(gateway_nodes, dict):
        raise ValueError("openclaw.json gateway.nodes section must be a JSON object")
    gateway_nodes.setdefault(
        "denyCommands",
        [
            "camera.snap",
            "camera.clip",
            "screen.record",
            "contacts.add",
            "calendar.add",
            "reminders.add",
            "sms.send",
        ],
    )

    auth_profiles = openclaw.get("authProfiles", {})
    if auth_profiles:
        auth_section = config.setdefault("auth", {})
        if not isinstance(auth_section, dict):
            raise ValueError("openclaw.json auth section must be a JSON object")
        config_profiles = auth_section.setdefault("profiles", {})
        if not isinstance(config_profiles, dict):
            raise ValueError("openclaw.json auth.profiles must be a JSON object")
        for profile_name, profile_config in auth_profiles.items():
            if not isinstance(profile_config, dict):
                raise ValueError("openclaw.authProfiles entries must be JSON objects")
            provider = str(profile_config.get("provider", "")).strip()
            if not provider:
                raise ValueError(f"openclaw.authProfiles.{profile_name}.provider is required")
            config_profiles[profile_name] = {
                "provider": provider,
                "mode": "api_key",
            }

    slack = _extract_slack_manifest(manifest)
    if slack:
        channels_section = config.setdefault("channels", {})
        if not isinstance(channels_section, dict):
            raise ValueError("openclaw.json channels section must be a JSON object")
        slack_config = channels_section.setdefault("slack", {})
        if not isinstance(slack_config, dict):
            raise ValueError("openclaw.json channels.slack must be a JSON object")
        for key, value in slack.items():
            if key == "allowFrom":
                continue
            slack_config[key] = value


def apply_openclaw_auth_profiles(auth_profiles: dict[str, object], manifest: dict[str, object]) -> None:
    openclaw = manifest.get("openclaw", {})
    if not isinstance(openclaw, dict):
        return
    requested_profiles = openclaw.get("authProfiles", {})
    if not requested_profiles:
        return
    profiles_section = auth_profiles.setdefault("profiles", {})
    usage_stats = auth_profiles.setdefault("usageStats", {})
    auth_profiles.setdefault("version", 1)
    if not isinstance(profiles_section, dict):
        raise ValueError("auth-profiles.json profiles must be a JSON object")
    if not isinstance(usage_stats, dict):
        raise ValueError("auth-profiles.json usageStats must be a JSON object")

    for profile_name, profile_config in requested_profiles.items():
        if not isinstance(profile_config, dict):
            raise ValueError("openclaw.authProfiles entries must be JSON objects")
        provider = str(profile_config.get("provider", "")).strip()
        key_env = str(profile_config.get("keyEnv", "")).strip()
        profile_type = str(profile_config.get("type", "api_key")).strip() or "api_key"
        if not provider:
            raise ValueError(f"openclaw.authProfiles.{profile_name}.provider is required")
        if not key_env:
            raise ValueError(f"openclaw.authProfiles.{profile_name}.keyEnv is required")
        profiles_section[profile_name] = {
            "type": profile_type,
            "provider": provider,
            "keyRef": {
                "source": "env",
                "provider": "default",
                "id": key_env,
            },
        }
        usage_stats.setdefault(profile_name, {})


def _extract_slack_manifest(manifest: dict[str, object]) -> dict[str, object]:
    openclaw = manifest.get("openclaw", {})
    if not isinstance(openclaw, dict):
        return {}
    channels = openclaw.get("channels", {})
    if not isinstance(channels, dict):
        return {}
    slack = channels.get("slack", {})
    if not isinstance(slack, dict):
        return {}
    return dict(slack)


def ensure_shared_access_guest_runtime(mount_dir: Path, manifest: dict[str, object]) -> None:
    shared_access_config = extract_shared_access_config(manifest)
    if not shared_access_config:
        return
    state_dir = mount_dir / "home/admin/.openclaw"
    state_dir.mkdir(parents=True, exist_ok=True)
    shared_access_path = state_dir / "shared-access.json"
    shared_access_path.write_text(
        json.dumps(shared_access_config, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(shared_access_path, 0o600)
    helper_path = mount_dir / "usr/local/bin/openclaw-shared-access"
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_text(render_shared_access_script(), encoding="utf-8")
    os.chmod(helper_path, 0o755)
    lookup_path = mount_dir / "usr/local/bin/company-email-intro-lookup"
    lookup_path.write_text(render_email_intro_lookup_script(), encoding="utf-8")
    os.chmod(lookup_path, 0o755)
    draft_path = mount_dir / "usr/local/bin/company-email-intro-draft"
    draft_path.write_text(render_email_intro_draft_script(), encoding="utf-8")
    os.chmod(draft_path, 0o755)


def ensure_guest_package_runtime(mount_dir: Path) -> None:
    root_helper_path = mount_dir / "usr/local/sbin/openclaw-apt-install-root"
    root_helper_path.parent.mkdir(parents=True, exist_ok=True)
    root_helper_path.write_text(render_guest_package_root_helper(), encoding="utf-8")
    os.chmod(root_helper_path, 0o755)

    install_wrapper_path = mount_dir / "usr/local/bin/pkg-install"
    install_wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    install_wrapper_path.write_text(render_guest_package_install_wrapper(), encoding="utf-8")
    os.chmod(install_wrapper_path, 0o755)

    search_wrapper_path = mount_dir / "usr/local/bin/pkg-search"
    search_wrapper_path.write_text(render_guest_package_search_wrapper(), encoding="utf-8")
    os.chmod(search_wrapper_path, 0o755)

    sudoers_path = mount_dir / "etc/sudoers.d/openclaw-package-install"
    sudoers_path.parent.mkdir(parents=True, exist_ok=True)
    sudoers_path.write_text(
        "admin ALL=(root) NOPASSWD: /usr/local/sbin/openclaw-apt-install-root\n",
        encoding="utf-8",
    )
    os.chmod(sudoers_path, 0o440)


def ensure_guest_google_oauth_runtime(
    mount_dir: Path,
    *,
    client_path: Path = DEFAULT_GOOGLE_OAUTH_CLIENT_PATH,
) -> Path | None:
    if not client_path.exists():
        return None
    state_dir = mount_dir / "home/admin/.openclaw"
    workspace_dir = state_dir / "workspace"
    scripts_dir = workspace_dir / "scripts"
    secrets_dir = workspace_dir / "secrets"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)

    destination_client_path = secrets_dir / "google-oauth-client.json"
    shutil.copyfile(client_path, destination_client_path)
    os.chmod(destination_client_path, 0o600)

    start_script_path = scripts_dir / "gmail-oauth-start.mjs"
    start_script_path.write_text(render_google_oauth_start_script(), encoding="utf-8")
    os.chmod(start_script_path, 0o755)

    exchange_script_path = scripts_dir / "gmail-oauth-exchange.mjs"
    exchange_script_path.write_text(render_google_oauth_exchange_script(), encoding="utf-8")
    os.chmod(exchange_script_path, 0o755)

    connect_wrapper_path = mount_dir / "usr/local/bin/connect-google"
    connect_wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    connect_wrapper_path.write_text(render_google_connect_wrapper(), encoding="utf-8")
    os.chmod(connect_wrapper_path, 0o755)

    finish_wrapper_path = mount_dir / "usr/local/bin/finish-google"
    finish_wrapper_path.write_text(render_google_finish_wrapper(), encoding="utf-8")
    os.chmod(finish_wrapper_path, 0o755)

    status_wrapper_path = mount_dir / "usr/local/bin/google-auth-status"
    status_wrapper_path.write_text(render_google_auth_status_wrapper(), encoding="utf-8")
    os.chmod(status_wrapper_path, 0o755)
    return state_dir


def render_guest_package_root_helper() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: openclaw-apt-install-root <package> [package...]" >&2
  exit 2
fi

if [[ $# -gt 25 ]]; then
  echo "too many packages requested at once" >&2
  exit 2
fi

for pkg in "$@"; do
  if [[ "$pkg" == -* ]] || [[ ! "$pkg" =~ ^[a-z0-9][a-z0-9+.-]*$ ]]; then
    echo "invalid package name: $pkg" >&2
    exit 2
  fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "$@"
"""


def render_guest_package_install_wrapper() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
exec sudo -n /usr/local/sbin/openclaw-apt-install-root "$@"
"""


def render_guest_package_search_wrapper() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: pkg-search <query>" >&2
  exit 2
fi

exec apt-cache search -- "$*"
"""


def render_google_oauth_start_script() -> str:
    return """import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const workspace = '/home/admin/.openclaw/workspace';
const secretsDir = path.join(workspace, 'secrets');
const clientPath = path.join(secretsDir, 'google-oauth-client.json');
const statePath = path.join(secretsDir, 'google-gmail-oauth-state.json');

const raw = fs.readFileSync(clientPath, 'utf8');
const cfg = JSON.parse(raw);
const installed = cfg.installed || cfg.web;
if (!installed) throw new Error('Expected OAuth client JSON with installed or web key');

const clientId = installed.client_id;
const redirectUri = 'http://localhost:3000/callback';
const scope = [
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/calendar.readonly'
].join(' ');
const state = crypto.randomBytes(24).toString('hex');

fs.writeFileSync(statePath, JSON.stringify({
  state,
  redirectUri,
  scope,
  createdAt: new Date().toISOString()
}, null, 2));

const params = new URLSearchParams({
  client_id: clientId,
  redirect_uri: redirectUri,
  response_type: 'code',
  scope,
  access_type: 'offline',
  prompt: 'consent',
  state,
});

const url = `${installed.auth_uri}?${params.toString()}`;
console.log('Open this URL in a browser and approve read-only Gmail and Calendar access:');
console.log(url);
console.log('\\nAfter approval, Google will redirect to localhost and likely fail to load.');
console.log('Copy the full URL from the browser address bar and send it to me.');
"""


def render_google_oauth_exchange_script() -> str:
    return """import fs from 'node:fs';
import path from 'node:path';

const workspace = '/home/admin/.openclaw/workspace';
const secretsDir = path.join(workspace, 'secrets');
const clientPath = path.join(secretsDir, 'google-oauth-client.json');
const statePath = path.join(secretsDir, 'google-gmail-oauth-state.json');
const tokenPath = path.join(secretsDir, 'google-gmail-token.json');

const callbackUrlRaw = process.argv[2];
if (!callbackUrlRaw) {
  console.error('Usage: node scripts/gmail-oauth-exchange.mjs \"http://localhost:3000/callback?...\"');
  process.exit(1);
}

const callbackUrl = callbackUrlRaw.replaceAll('&amp;', '&');
const url = new URL(callbackUrl);
const code = url.searchParams.get('code');
const state = url.searchParams.get('state');
if (!code || !state) throw new Error('Missing code or state in callback URL');

const client = JSON.parse(fs.readFileSync(clientPath, 'utf8')).installed;
const expected = JSON.parse(fs.readFileSync(statePath, 'utf8'));
if (state !== expected.state) throw new Error('State mismatch');

const body = new URLSearchParams({
  code,
  client_id: client.client_id,
  client_secret: client.client_secret,
  redirect_uri: expected.redirectUri,
  grant_type: 'authorization_code'
});

const res = await fetch(client.token_uri, {
  method: 'POST',
  headers: { 'content-type': 'application/x-www-form-urlencoded' },
  body
});

const text = await res.text();
if (!res.ok) {
  console.error(text);
  process.exit(2);
}

const token = JSON.parse(text);
fs.writeFileSync(tokenPath, JSON.stringify({
  ...token,
  scope: expected.scope,
  obtainedAt: new Date().toISOString()
}, null, 2));

console.log(`Saved token to ${tokenPath}`);
console.log(`Has refresh token: ${Boolean(token.refresh_token)}`);
"""


def render_google_connect_wrapper() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
exec node /home/admin/.openclaw/workspace/scripts/gmail-oauth-start.mjs
"""


def render_google_finish_wrapper() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: finish-google '<callback_url>'" >&2
  exit 2
fi

exec node /home/admin/.openclaw/workspace/scripts/gmail-oauth-exchange.mjs "$1"
"""


def render_google_auth_status_wrapper() -> str:
    return """#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const workspace = '/home/admin/.openclaw/workspace';
const clientPath = path.join(workspace, 'secrets', 'google-oauth-client.json');
const tokenPath = path.join(workspace, 'secrets', 'google-gmail-token.json');

const result = {
  oauth_client_configured: fs.existsSync(clientPath),
  connected: false,
  scopes: [],
  next_step: null,
};

if (fs.existsSync(tokenPath)) {
  try {
    const token = JSON.parse(fs.readFileSync(tokenPath, 'utf8'));
    result.connected = true;
    const scope = typeof token.scope === 'string' ? token.scope : '';
    result.scopes = scope.split(/\\s+/).filter(Boolean);
  } catch (error) {
    result.next_step = 'Google token exists but could not be parsed.';
  }
}

if (!result.connected) {
  result.next_step = 'Run `connect-google`, complete the browser consent flow, then run `finish-google \"<callback_url>\"`.';
}

process.stdout.write(JSON.stringify(result, null, 2) + '\\n');
"""


def render_shared_access_script() -> str:
    return r"""#!/usr/bin/env node
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const CONFIG_PATH = "/home/admin/.openclaw/shared-access.json";
const ALLOWED_ACTIONS = new Set(["email_intro_lookup", "draft_intro_from_email_context"]);
const RAW_FIELD_NAMES = new Set(["body", "raw_body", "content", "snippet", "excerpt", "attachments"]);

function fail(message, code = 1) {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

function readStdin() {
  return fs.readFileSync(0, "utf8");
}

function loadJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function sanitizeReference(reference) {
  if (!reference || typeof reference !== "object" || Array.isArray(reference)) {
    throw new Error("references entries must be JSON objects");
  }
  for (const key of Object.keys(reference)) {
    if (RAW_FIELD_NAMES.has(key)) {
      throw new Error(`disallowed raw content field in reference: ${key}`);
    }
  }
  const allowed = ["message_id", "thread_id", "subject", "sender", "recipients", "date"];
  const output = {};
  for (const key of allowed) {
    if (key in reference) {
      output[key] = reference[key];
    }
  }
  return output;
}

function sanitizeLookupResult(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("lookup result must be a JSON object");
  }
  for (const key of Object.keys(raw)) {
    if (RAW_FIELD_NAMES.has(key)) {
      throw new Error(`disallowed raw content field in result: ${key}`);
    }
  }
  const required = ["decision_summary", "why_relevant", "relationship_assessment", "suggested_next_step"];
  const output = {};
  for (const key of required) {
    output[key] = typeof raw[key] === "string" ? raw[key] : "";
  }
  const references = Array.isArray(raw.references) ? raw.references : [];
  output.references = references.map(sanitizeReference);
  return output;
}

function sanitizeDraftResult(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("draft result must be a JSON object");
  }
  for (const key of Object.keys(raw)) {
    if (RAW_FIELD_NAMES.has(key)) {
      throw new Error(`disallowed raw content field in draft result: ${key}`);
    }
  }
  return {
    draft_intro: typeof raw.draft_intro === "string" ? raw.draft_intro : "",
    rationale: typeof raw.rationale === "string" ? raw.rationale : "",
  };
}

function applyTemplate(args, requestPath, responsePath) {
  return args.map((value) =>
    String(value)
      .replaceAll("{request_path}", requestPath)
      .replaceAll("{response_path}", responsePath)
  );
}

function runConfiguredCommand(commandConfig, request) {
  if (!Array.isArray(commandConfig) || commandConfig.length === 0) {
    throw new Error("shared access command must be a non-empty array");
  }
  const requestPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "openclaw-shared-access-")), "request.json");
  const responsePath = path.join(path.dirname(requestPath), "response.json");
  fs.writeFileSync(requestPath, JSON.stringify(request, null, 2) + "\n", "utf8");
  const args = applyTemplate(commandConfig.slice(1), requestPath, responsePath);
  const env = { ...process.env, OPENCLAW_SHARED_ACCESS_REQUEST_PATH: requestPath, OPENCLAW_SHARED_ACCESS_RESPONSE_PATH: responsePath };
  const result = spawnSync(String(commandConfig[0]), args, { encoding: "utf8", env });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "shared access command failed");
  }
  if (fs.existsSync(responsePath)) {
    return JSON.parse(fs.readFileSync(responsePath, "utf8"));
  }
  if (!result.stdout.trim()) {
    throw new Error("shared access command returned no JSON output");
  }
  return JSON.parse(result.stdout);
}

function main() {
  if (process.argv[2] !== "execute") {
    fail("usage: openclaw-shared-access execute");
  }
  if (!fs.existsSync(CONFIG_PATH)) {
    fail("shared access is not configured");
  }
  const config = loadJson(CONFIG_PATH);
  if (!config.opt_in) {
    fail("shared access is not enabled for this user");
  }
  const request = JSON.parse(readStdin());
  const actionType = String(request.action_type || "");
  if (!ALLOWED_ACTIONS.has(actionType)) {
    fail("unsupported shared access action");
  }
  const capabilities = Array.isArray(config.capabilities) ? config.capabilities.map(String) : [];
  if (!capabilities.includes(actionType)) {
    fail("shared access capability is not enabled");
  }

  const actionConfig = config[actionType];
  if (!actionConfig || typeof actionConfig !== "object" || Array.isArray(actionConfig)) {
    fail("shared access capability is misconfigured");
  }
  const rawResult = runConfiguredCommand(actionConfig.command, request);
  const sanitized = actionType === "draft_intro_from_email_context"
    ? sanitizeDraftResult(rawResult)
    : sanitizeLookupResult(rawResult);
  process.stdout.write(JSON.stringify(sanitized, null, 2) + "\n");
}

try {
  main();
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
"""


def render_email_intro_lookup_script() -> str:
    return r"""#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const SHARED_ACCESS_CONFIG_PATH = "/home/admin/.openclaw/shared-access.json";

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 2) {
    args[argv[i]] = argv[i + 1];
  }
  if (!args["--request"] || !args["--response"]) {
    fail("usage: company-email-intro-lookup --request <path> --response <path>");
  }
  return args;
}

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }
  const result = {};
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    if (!line || line.trim().startsWith("#")) {
      continue;
    }
    const index = line.indexOf("=");
    if (index === -1) {
      continue;
    }
    result[line.slice(0, index)] = line.slice(index + 1);
  }
  return result;
}

function loadSharedAccessConfig() {
  if (!fs.existsSync(SHARED_ACCESS_CONFIG_PATH)) {
    return {};
  }
  return JSON.parse(fs.readFileSync(SHARED_ACCESS_CONFIG_PATH, "utf8"));
}

function extractJsonObject(text) {
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first === -1 || last === -1 || last <= first) {
    throw new Error("OpenClaw did not return a JSON object");
  }
  return JSON.parse(text.slice(first, last + 1));
}

function normalizeMailbox(value) {
  return String(value || "").trim().toLowerCase();
}

function mailboxScope() {
  const sharedAccess = loadSharedAccessConfig();
  const lookup = sharedAccess.email_intro_lookup;
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return [];
  }
  const raw = Array.isArray(lookup.allowedRecipientFilters) ? lookup.allowedRecipientFilters : [];
  return raw.map(normalizeMailbox).filter(Boolean);
}

function recipientsWithinScope(reference, allowedFilters) {
  if (!allowedFilters.length) {
    return true;
  }
  const haystack = [
    reference.sender,
    reference.recipients,
  ]
    .filter((value) => typeof value === "string" && value.trim())
    .join(" ")
    .toLowerCase();
  return allowedFilters.some((mailbox) => haystack.includes(mailbox));
}

function main() {
  const args = parseArgs(process.argv);
  const request = JSON.parse(fs.readFileSync(args["--request"], "utf8"));
  const allowedFilters = mailboxScope();
  const scopeText = allowedFilters.length
    ? `Only use emails where sender or recipients include one of these mailboxes: ${allowedFilters.join(", ")}.`
    : "No mailbox recipient filter is configured.";
  const prompt = [
    "You are a scoped shared email lookup tool.",
    "Use only the local user's email access and do not read unrelated sources if email is enough.",
    "Search only the last 1 year of email history.",
    "Build context from that 1-year window on the requested subject.",
    "Unless the requester explicitly asks otherwise, focus the summary on the most recent few relevant emails.",
    scopeText,
    "Do not reveal raw email bodies, snippets, or attachments.",
    "Return strict JSON with exactly these keys:",
    "decision_summary, why_relevant, relationship_assessment, suggested_next_step, references.",
    "Each reference entry may only include: message_id, thread_id, subject, sender, recipients, date.",
    `Requester slack id: ${request.requester_slack_user_id || ""}`,
    `Purpose: ${request.purpose || ""}`,
    `Entity name: ${request.entity_name || ""}`,
    `Entity company: ${request.entity_company || ""}`,
    "If the evidence is weak, say so explicitly.",
  ].join("\n");
  const env = {
    ...process.env,
    ...loadEnv("/home/admin/.openclaw/.env"),
    HOME: "/home/admin",
  };
    const result = spawnSync(
      "openclaw",
      [
        "agent",
        "--agent",
        "main",
        "--local",
        "--message",
        prompt,
        "--thinking",
        "medium",
    ],
    {
      encoding: "utf8",
      env,
      timeout: 600000,
    }
  );
  if (result.status !== 0) {
    fail(result.stderr || result.stdout || "openclaw agent failed");
  }
  const parsed = extractJsonObject(result.stdout);
  const references = Array.isArray(parsed.references) ? parsed.references : [];
  parsed.references = references.filter((reference) => recipientsWithinScope(reference, allowedFilters));
  if (allowedFilters.length && parsed.references.length === 0) {
    throw new Error(
      `lookup returned no references within allowed mailbox scope (${allowedFilters.join(", ")})`
    );
  }
  fs.writeFileSync(args["--response"], JSON.stringify(parsed, null, 2) + "\n", "utf8");
}

try {
  main();
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
"""


def render_email_intro_draft_script() -> str:
    return r"""#!/usr/bin/env node
const fs = require("fs");
const { spawnSync } = require("child_process");

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 2) {
    args[argv[i]] = argv[i + 1];
  }
  if (!args["--request"] || !args["--response"]) {
    fail("usage: company-email-intro-draft --request <path> --response <path>");
  }
  return args;
}

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }
  const result = {};
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    if (!line || line.trim().startsWith("#")) {
      continue;
    }
    const index = line.indexOf("=");
    if (index === -1) {
      continue;
    }
    result[line.slice(0, index)] = line.slice(index + 1);
  }
  return result;
}

function extractJsonObject(text) {
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first === -1 || last === -1 || last <= first) {
    throw new Error("OpenClaw did not return a JSON object");
  }
  return JSON.parse(text.slice(first, last + 1));
}

function main() {
  const args = parseArgs(process.argv);
  const request = JSON.parse(fs.readFileSync(args["--request"], "utf8"));
  const approvedContext = JSON.stringify(request.approved_context || {}, null, 2);
  const prompt = [
    "You are a scoped intro drafting tool.",
    "Use only the approved structured context below.",
    "Do not access email tools or any other external data.",
    "Return strict JSON with exactly these keys: draft_intro, rationale.",
    `Approved context:\n${approvedContext}`,
    `Requester slack id: ${request.requester_slack_user_id || ""}`,
    `Purpose: ${request.purpose || ""}`,
    `Entity name: ${request.entity_name || ""}`,
    `Entity company: ${request.entity_company || ""}`,
  ].join("\n\n");
  const env = {
    ...process.env,
    ...loadEnv("/home/admin/.openclaw/.env"),
    HOME: "/home/admin",
  };
    const result = spawnSync(
      "openclaw",
      [
        "agent",
        "--agent",
        "main",
        "--local",
        "--message",
        prompt,
        "--thinking",
        "medium",
    ],
    {
      encoding: "utf8",
      env,
      timeout: 600000,
    }
  );
  if (result.status !== 0) {
    fail(result.stderr || result.stdout || "openclaw agent failed");
  }
  const parsed = extractJsonObject(result.stdout);
  fs.writeFileSync(args["--response"], JSON.stringify(parsed, null, 2) + "\n", "utf8");
}

try {
  main();
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
"""


def ensure_gateway_user_service(
    mount_dir: Path,
    systemd_user_dir: Path,
    wants_dir: Path,
    openclaw_config: dict[str, object],
) -> None:
    gateway = openclaw_config.get("gateway", {})
    if not isinstance(gateway, dict):
        raise ValueError("openclaw.json gateway section must be a JSON object")
    port = int(gateway.get("port", 18789))
    service_path = systemd_user_dir / "openclaw-gateway.service"
    service_path.write_text(
        render_gateway_service_unit(port),
        encoding="utf-8",
    )
    os.chmod(service_path, 0o644)
    wants_link = wants_dir / "openclaw-gateway.service"
    wants_link.unlink(missing_ok=True)
    os.symlink("/home/admin/.config/systemd/user/openclaw-gateway.service", wants_link)
    linger_path = mount_dir / "var/lib/systemd/linger/admin"
    linger_path.touch(exist_ok=True)
    os.chmod(linger_path, 0o644)


def ensure_gateway_activation_service(mount_dir: Path, bootstrap_state_dir: Path) -> None:
    script_path = mount_dir / "usr/local/sbin/openclaw-activation-refresh.sh"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        render_gateway_activation_script(),
        encoding="utf-8",
    )
    os.chmod(script_path, 0o755)

    service_path = mount_dir / "etc/systemd/system/openclaw-activation-refresh.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_gateway_activation_service(),
        encoding="utf-8",
    )
    os.chmod(service_path, 0o644)

    wants_dir = mount_dir / "etc/systemd/system/multi-user.target.wants"
    wants_dir.mkdir(parents=True, exist_ok=True)
    wants_link = wants_dir / "openclaw-activation-refresh.service"
    wants_link.unlink(missing_ok=True)
    os.symlink("/etc/systemd/system/openclaw-activation-refresh.service", wants_link)

    pending_flag = bootstrap_state_dir / "pending-gateway-restart"
    pending_flag.touch(exist_ok=True)
    os.chmod(pending_flag, 0o644)


def render_gateway_service_unit(port: int) -> str:
    return (
        "[Unit]\n"
        "Description=OpenClaw Gateway\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart=/usr/bin/node /usr/lib/node_modules/openclaw/dist/index.js gateway --port {port}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "TimeoutStopSec=30\n"
        "TimeoutStartSec=30\n"
        "SuccessExitStatus=0 143\n"
        "KillMode=control-group\n"
        "Environment=HOME=/home/admin\n"
        "Environment=TMPDIR=/tmp\n"
        "Environment=PATH=/home/admin/.local/bin:/home/admin/.npm-global/bin:/home/admin/bin:/home/admin/.volta/bin:/home/admin/.asdf/shims:/home/admin/.bun/bin:/home/admin/.nvm/current/bin:/home/admin/.fnm/current/bin:/home/admin/.local/share/pnpm:/usr/local/bin:/usr/bin:/bin\n"
        f"Environment=OPENCLAW_GATEWAY_PORT={port}\n"
        "Environment=OPENCLAW_SYSTEMD_UNIT=openclaw-gateway.service\n"
        "Environment=\"OPENCLAW_WINDOWS_TASK_NAME=OpenClaw Gateway\"\n"
        "Environment=OPENCLAW_SERVICE_MARKER=openclaw\n"
        "Environment=OPENCLAW_SERVICE_KIND=gateway\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_gateway_activation_service() -> str:
    return (
        "[Unit]\n"
        "Description=Apply pending OpenClaw activation changes\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/local/sbin/openclaw-activation-refresh.sh\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_gateway_activation_script() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "STATE_DIR=/var/lib/openclaw/bootstrap\n"
        "PENDING_FLAG=\"${STATE_DIR}/pending-gateway-restart\"\n"
        "ADMIN_USER=admin\n\n"
        "if [[ ! -f \"${PENDING_FLAG}\" ]]; then\n"
        "  exit 0\n"
        "fi\n\n"
        "if ! id \"${ADMIN_USER}\" >/dev/null 2>&1; then\n"
        "  rm -f \"${PENDING_FLAG}\"\n"
        "  exit 0\n"
        "fi\n\n"
        "if [[ ! -f /home/admin/.config/systemd/user/openclaw-gateway.service ]]; then\n"
        "  rm -f \"${PENDING_FLAG}\"\n"
        "  exit 0\n"
        "fi\n\n"
        "ADMIN_UID=$(id -u \"${ADMIN_USER}\")\n"
        "ADMIN_GID=$(id -g \"${ADMIN_USER}\")\n"
        "export XDG_RUNTIME_DIR=\"/run/user/${ADMIN_UID}\"\n"
        "mkdir -p \"${XDG_RUNTIME_DIR}\"\n"
        "chown \"${ADMIN_UID}:${ADMIN_GID}\" \"${XDG_RUNTIME_DIR}\"\n"
        "chmod 0700 \"${XDG_RUNTIME_DIR}\"\n\n"
        "systemctl start \"user@${ADMIN_UID}.service\"\n\n"
        "run_as_admin() {\n"
        "  runuser -u \"${ADMIN_USER}\" -- env \\\n"
        "    XDG_RUNTIME_DIR=\"${XDG_RUNTIME_DIR}\" \\\n"
        "    DBUS_SESSION_BUS_ADDRESS=\"unix:path=${XDG_RUNTIME_DIR}/bus\" \\\n"
        "    \"$@\"\n"
        "}\n\n"
        "run_as_admin systemctl --user daemon-reload\n"
        "run_as_admin systemctl --user enable openclaw-gateway.service >/dev/null 2>&1 || true\n"
        "if ! run_as_admin systemctl --user restart openclaw-gateway.service; then\n"
        "  run_as_admin openclaw gateway restart\n"
        "fi\n\n"
        "rm -f \"${PENDING_FLAG}\"\n"
    )


def timestamp_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this command must run as root")
