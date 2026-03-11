from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
        network = render_guest_network(config, "172.31.0.15")
        self.assertIn("Address=172.31.0.15/24", network)
        self.assertIn("Gateway=172.31.0.1", network)
        self.assertIn("DNS=1.1.1.1", network)

    def test_sanitize_snapshot_label(self) -> None:
        self.assertEqual(sanitize_snapshot_label("Before Upgrade!"), "before-upgrade")


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
