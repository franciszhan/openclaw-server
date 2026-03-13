from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openclaw_hostctl.config import save_user_record
from openclaw_hostctl.hostctl import HostController, render_guest_network
from openclaw_hostctl.models import HostConfig, UserRecord
from openclaw_hostctl.firecracker import make_mac_address, make_tap_name, render_firecracker_config
from openclaw_hostctl.disk import sanitize_snapshot_label


def example_config(storage_root: Path) -> HostConfig:
    return HostConfig(
        storage_root=storage_root,
        firecracker_bin=Path("/usr/local/bin/firecracker"),
        kernel_image=storage_root / "base/vmlinux",
        base_rootfs=storage_root / "base/openclaw-base.ext4",
        storage_copy_mode="reflink",
        bridge_name="ocbr0",
        bridge_cidr="172.31.0.1/24",
        first_guest_ip="172.31.0.10",
        last_guest_ip="172.31.0.20",
        guest_dns=["1.1.1.1", "1.0.0.1"],
        vcpu_count=2,
        mem_mib=4096,
        smt=False,
        allow_guest_egress=True,
        admin_ssh_keys_path=storage_root / "admin_authorized_keys",
        shared_skills_dir=None,
        loop_mount_base=storage_root / "mnt",
    )


class HostControllerTests(unittest.TestCase):
    def test_allocate_guest_ip_uses_first_free_address(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = example_config(Path(tmp_dir))
            controller = HostController(config)
            controller.init_layout()
            self.assertEqual(str(controller._allocate_guest_ip()), "172.31.0.10")

    def test_render_guest_network_uses_static_addressing(self) -> None:
        config = example_config(Path("/tmp/openclaw"))
        network = render_guest_network(config, "172.31.0.15", "06:00:ac:1f:00:0f")
        self.assertIn("MACAddress=06:00:ac:1f:00:0f", network)
        self.assertIn("Address=172.31.0.15/24", network)
        self.assertIn("Gateway=172.31.0.1", network)
        self.assertIn("DNS=1.1.1.1", network)

    def test_sanitize_snapshot_label(self) -> None:
        self.assertEqual(sanitize_snapshot_label("Before Upgrade!"), "before-upgrade")

    def test_activate_user_reuses_stored_inputs_when_not_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_root = Path(tmp_dir)
            config = example_config(storage_root)
            controller = HostController(config)
            controller.init_layout()
            record = UserRecord(
                user_id="alice",
                display_name="Alice",
                machine_name="openclaw-alice",
                ip_address="172.31.0.10",
                mac_address="06:00:ac:1f:00:0a",
                tap_name="ocalice",
                rootfs_path=str(config.user_rootfs_path("alice")),
                created_at="2026-03-10T00:00:00Z",
            )
            config.user_dir("alice").mkdir(parents=True, exist_ok=True)
            save_user_record(config.user_record_path("alice"), record)
            Path(record.rootfs_path).write_text("image", encoding="utf-8")
            config.user_config_store_path("alice").write_text(
                json.dumps({"profile": "default"}) + "\n",
                encoding="utf-8",
            )
            config.activation_config_store_path("alice").write_text(
                json.dumps({"slack_user_id": "U123"}) + "\n",
                encoding="utf-8",
            )
            config.activation_secrets_store_path("alice").write_text(
                "OPENAI_API_KEY=secret\n",
                encoding="utf-8",
            )
            mount_dir = storage_root / "mounts/alice"
            mount_dir.mkdir(parents=True)

            @contextlib.contextmanager
            def fake_mount(_image: Path, _mount_base: Path, _mount_name: str):
                yield mount_dir

            with (
                mock.patch("openclaw_hostctl.hostctl.require_root"),
                mock.patch("openclaw_hostctl.hostctl.mounted_image", fake_mount),
                mock.patch.object(HostController, "is_running", return_value=False),
            ):
                result = controller.activate_user(
                    "alice",
                    user_config_path=None,
                    activation_config_path=None,
                    secrets_env_path=None,
                    force=False,
                    restart=False,
                )

            self.assertEqual(result["user_id"], "alice")
            self.assertFalse(result["restarted"])
            self.assertEqual(
                (mount_dir / "etc/openclaw/config.json").read_text(encoding="utf-8").strip(),
                '{"profile": "default"}',
            )
            self.assertEqual(
                (mount_dir / "etc/openclaw/activation.json").read_text(encoding="utf-8").strip(),
                '{"slack_user_id": "U123"}',
            )
            self.assertEqual(
                (mount_dir / "etc/openclaw/secrets.env").read_text(encoding="utf-8"),
                "OPENAI_API_KEY=secret\n",
            )

    def test_activate_user_persists_new_inputs_for_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_root = Path(tmp_dir)
            config = example_config(storage_root)
            controller = HostController(config)
            controller.init_layout()
            record = UserRecord(
                user_id="alice",
                display_name="Alice",
                machine_name="openclaw-alice",
                ip_address="172.31.0.10",
                mac_address="06:00:ac:1f:00:0a",
                tap_name="ocalice",
                rootfs_path=str(config.user_rootfs_path("alice")),
                created_at="2026-03-10T00:00:00Z",
            )
            config.user_dir("alice").mkdir(parents=True, exist_ok=True)
            save_user_record(config.user_record_path("alice"), record)
            Path(record.rootfs_path).write_text("image", encoding="utf-8")
            mount_dir = storage_root / "mounts/alice"
            mount_dir.mkdir(parents=True)
            new_user_config = storage_root / "incoming-user-config.json"
            new_user_config.write_text('{"profile": "prod"}\n', encoding="utf-8")
            new_activation_config = storage_root / "incoming-activation-config.json"
            new_activation_config.write_text('{"slack_user_id": "U999"}\n', encoding="utf-8")
            new_secrets = storage_root / "incoming-secrets.env"
            new_secrets.write_text("OPENAI_API_KEY=prod\n", encoding="utf-8")

            @contextlib.contextmanager
            def fake_mount(_image: Path, _mount_base: Path, _mount_name: str):
                yield mount_dir

            with (
                mock.patch("openclaw_hostctl.hostctl.require_root"),
                mock.patch("openclaw_hostctl.hostctl.mounted_image", fake_mount),
                mock.patch.object(HostController, "is_running", return_value=False),
            ):
                controller.activate_user(
                    "alice",
                    user_config_path=new_user_config,
                    activation_config_path=new_activation_config,
                    secrets_env_path=new_secrets,
                    force=False,
                    restart=False,
                )

            self.assertEqual(
                config.user_config_store_path("alice").read_text(encoding="utf-8"),
                '{"profile": "prod"}\n',
            )
            self.assertEqual(
                config.activation_config_store_path("alice").read_text(encoding="utf-8"),
                '{"slack_user_id": "U999"}\n',
            )
            self.assertEqual(
                config.activation_secrets_store_path("alice").read_text(encoding="utf-8"),
                "OPENAI_API_KEY=prod\n",
            )


class FirecrackerTests(unittest.TestCase):
    def test_make_tap_name_is_stable_and_short(self) -> None:
        tap_name = make_tap_name("alice-example")
        self.assertLessEqual(len(tap_name), 15)
        self.assertEqual(tap_name, make_tap_name("alice-example"))

    def test_make_mac_address_uses_guest_ip(self) -> None:
        self.assertEqual(make_mac_address("172.31.0.10"), "06:00:ac:1f:00:0a")

    def test_render_firecracker_config(self) -> None:
        config = example_config(Path("/tmp/openclaw"))
        user = UserRecord(
            user_id="alice",
            display_name="Alice",
            machine_name="openclaw-alice",
            ip_address="172.31.0.10",
            mac_address="06:00:ac:1f:00:0a",
            tap_name="oc1234567890",
            rootfs_path="/var/lib/openclaw/vms/alice/rootfs.ext4",
            created_at="2026-03-10T00:00:00Z",
        )
        rendered = render_firecracker_config(config, user)
        self.assertEqual(rendered["machine-config"]["mem_size_mib"], 4096)
        self.assertEqual(rendered["network-interfaces"][0]["guest_mac"], user.mac_address)
        self.assertIn("root=/dev/vda", rendered["boot-source"]["boot_args"])


if __name__ == "__main__":
    unittest.main()
