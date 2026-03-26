from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openclaw_hostctl.config import save_user_record
from openclaw_hostctl.hostctl import (
    HostController,
    ensure_guest_google_oauth_runtime,
    ensure_company_agents_addendum,
    render_email_intro_lookup_script,
    render_guest_network,
    render_google_auth_status_wrapper,
    render_company_agents_addendum,
    render_google_connect_wrapper,
    render_google_finish_wrapper,
)
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
        automation_ssh_private_key_path=storage_root / "automation_guest_key",
        automation_ssh_public_key_path=storage_root / "automation_guest_key.pub",
        shared_skills_dir=None,
        loop_mount_base=storage_root / "mnt",
    )


class HostControllerTests(unittest.TestCase):
    def test_google_oauth_wrappers_use_host_side_broker(self) -> None:
        status_script = render_google_auth_status_wrapper(
            host_ip="172.31.0.1",
            broker_user="openclaw-google-broker",
        )
        connect_wrapper = render_google_connect_wrapper(
            host_ip="172.31.0.1",
            broker_user="openclaw-google-broker",
        )
        finish_wrapper = render_google_finish_wrapper(
            host_ip="172.31.0.1",
            broker_user="openclaw-google-broker",
        )
        self.assertIn("openclaw-google-broker@172.31.0.1 start", connect_wrapper)
        self.assertIn("finish-b64", finish_wrapper)
        self.assertIn("python3 -c", finish_wrapper)
        self.assertIn("openclaw-google-broker@172.31.0.1 status", status_script)
        self.assertNotIn("google-oauth-client.json", connect_wrapper)

    def test_ensure_guest_google_oauth_runtime_installs_broker_wrappers_without_client_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_dir = Path(tmp_dir) / "mount"
            (mount_dir / "etc").mkdir(parents=True, exist_ok=True)
            (mount_dir / "etc/passwd").write_text(
                "admin:x:1000:1000::/home/admin:/bin/bash\n",
                encoding="utf-8",
            )
            (mount_dir / "home/admin").mkdir(parents=True, exist_ok=True)
            with mock.patch("openclaw_hostctl.hostctl.chown_tree"):
                state_dir = ensure_guest_google_oauth_runtime(
                    mount_dir,
                    user_id="alice",
                    broker_private_key="PRIVATE KEY\n",
                    host_ip="172.31.0.1",
                    known_hosts_line="172.31.0.1 ssh-ed25519 AAAABROKERHOSTKEY",
                )
            self.assertEqual(state_dir, mount_dir / "home/admin/.openclaw")
            self.assertTrue((mount_dir / "usr/local/bin/connect-google").exists())
            self.assertTrue((mount_dir / "usr/local/bin/finish-google").exists())
            self.assertTrue((mount_dir / "usr/local/bin/google-auth-status").exists())
            self.assertFalse(
                (mount_dir / "home/admin/.openclaw/workspace/scripts/gmail-oauth-start.mjs").exists()
            )
            self.assertFalse(
                (mount_dir / "home/admin/.openclaw/workspace/scripts/gmail-oauth-exchange.mjs").exists()
            )
            self.assertEqual(
                (mount_dir / "home/admin/.ssh/openclaw-google-broker").read_text(encoding="utf-8"),
                "PRIVATE KEY\n",
            )
            self.assertEqual(
                (
                    mount_dir / "home/admin/.ssh/openclaw-google-broker-known_hosts"
                ).read_text(encoding="utf-8"),
                "172.31.0.1 ssh-ed25519 AAAABROKERHOSTKEY\n",
            )
            self.assertFalse(
                (mount_dir / "home/admin/.openclaw/workspace/secrets/google-oauth-client.json").exists()
            )
            self.assertFalse(
                (mount_dir / "home/admin/.openclaw/workspace/secrets/google-gmail-token.json").exists()
            )

    def test_lookup_script_scopes_time_window_and_recent_focus(self) -> None:
        script = render_email_intro_lookup_script()
        self.assertIn("Search only the last 1 year of email history.", script)
        self.assertIn("Build context from that 1-year window on the requested subject.", script)
        self.assertIn(
            "Unless the requester explicitly asks otherwise, focus the summary on the most recent few relevant emails.",
            script,
        )
        self.assertIn("Read at most 3 attachments total", script)
        self.assertIn("raw attachment contents", script)
        self.assertIn('"low"', script)

    def test_company_agents_addendum_appends_without_replacing_existing_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_dir = Path(tmp_dir) / "mount"
            agents_path = mount_dir / "home/admin/.openclaw/workspace/AGENTS.md"
            agents_path.parent.mkdir(parents=True, exist_ok=True)
            agents_path.write_text("# AGENTS.md\n\nOriginal instructions.\n", encoding="utf-8")
            ensure_company_agents_addendum(mount_dir)
            content = agents_path.read_text(encoding="utf-8")
            self.assertIn("Original instructions.", content)
            self.assertIn("## Company Addendum", content)
            self.assertIn("/opt/openclaw/skills/company", content)
            self.assertIn("connect-google", content)

    def test_company_agents_addendum_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_dir = Path(tmp_dir) / "mount"
            agents_path = mount_dir / "home/admin/.openclaw/workspace/AGENTS.md"
            agents_path.parent.mkdir(parents=True, exist_ok=True)
            agents_path.write_text("# AGENTS.md\n", encoding="utf-8")
            ensure_company_agents_addendum(mount_dir)
            ensure_company_agents_addendum(mount_dir)
            content = agents_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("## Company Addendum"), 1)
            self.assertEqual(
                content.count(render_company_agents_addendum().splitlines()[0]),
                1,
            )

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

    def test_provision_guest_config_strips_openclaw_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_root = Path(tmp_dir)
            config = example_config(storage_root)
            controller = HostController(config)
            mount_dir = storage_root / "mount"
            (mount_dir / "home/admin").mkdir(parents=True, exist_ok=True)
            (mount_dir / "etc").mkdir(parents=True, exist_ok=True)
            (mount_dir / "etc/passwd").write_text(
                "admin:x:1000:1000::/home/admin:/bin/bash\n",
                encoding="utf-8",
            )
            config.admin_ssh_keys_path.parent.mkdir(parents=True, exist_ok=True)
            config.admin_ssh_keys_path.write_text("ssh-ed25519 AAAATEST admin@test\n", encoding="utf-8")
            manifest_path = storage_root / "alice-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "user_id": "alice",
                        "profile": "default",
                        "timezone": "America/Toronto",
                        "openclaw": {
                            "env": {
                                "OPENAI_API_KEY": "sk-test",
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            user = UserRecord(
                user_id="alice",
                display_name="Alice",
                machine_name="openclaw-alice",
                ip_address="172.31.0.10",
                mac_address="06:00:ac:1f:00:0a",
                tap_name="ocalice",
                rootfs_path=str(config.user_rootfs_path("alice")),
                created_at="2026-03-10T00:00:00Z",
            )

            with mock.patch("openclaw_hostctl.hostctl.chown_tree"):
                controller._seed_guest_files(mount_dir, user, manifest_path)

            guest_config = json.loads((mount_dir / "etc/openclaw/config.json").read_text(encoding="utf-8"))
            self.assertEqual(guest_config["user_id"], "alice")
            self.assertEqual(guest_config["profile"], "default")
            self.assertNotIn("openclaw", guest_config)

    def test_activate_user_manifest_writes_openclaw_runtime_files(self) -> None:
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
                json.dumps(
                    {
                        "user_id": "alice",
                        "profile": "default",
                        "openclaw": {
                            "env": {
                                "OPENAI_API_KEY": "sk-secret",
                            },
                            "authProfiles": {
                                "openai:default": {
                                    "provider": "openai",
                                    "type": "api_key",
                                    "keyEnv": "OPENAI_API_KEY",
                                }
                            },
                            "channels": {
                                "slack": {
                                    "enabled": True,
                                    "botToken": "xoxb-test",
                                    "appToken": "xapp-test",
                                    "allowFrom": ["U123"],
                                }
                            },
                        },
                        "slack_user_id": "UOWNER",
                        "shared_access": {
                            "opt_in": True,
                            "capabilities": ["email_intro_lookup"],
                            "email_intro_lookup": {
                                "command": ["python3", "lookup.py", "{request_path}", "{response_path}"],
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            mount_dir = storage_root / "mounts/alice"
            mount_dir.mkdir(parents=True)
            (mount_dir / "etc").mkdir(parents=True, exist_ok=True)
            (mount_dir / "etc/passwd").write_text(
                "admin:x:1000:1000::/home/admin:/bin/bash\n",
                encoding="utf-8",
            )
            (mount_dir / "home/admin").mkdir(parents=True, exist_ok=True)

            @contextlib.contextmanager
            def fake_mount(_image: Path, _mount_base: Path, _mount_name: str):
                yield mount_dir

            with (
                mock.patch("openclaw_hostctl.hostctl.require_root"),
                mock.patch("openclaw_hostctl.hostctl.mounted_image", fake_mount),
                mock.patch("openclaw_hostctl.hostctl.chown_tree"),
                mock.patch("openclaw_hostctl.hostctl.os.chown"),
                mock.patch.object(HostController, "is_running", return_value=False),
            ):
                result = controller.activate_user(
                    "alice",
                    manifest_path=None,
                    force=False,
                    restart=False,
                )

            self.assertEqual(result["user_id"], "alice")
            self.assertFalse(result["restarted"])
            self.assertEqual(
                (mount_dir / "etc/openclaw/config.json").read_text(encoding="utf-8").strip(),
                '{\n  "user_id": "alice",\n  "profile": "default"\n}',
            )
            self.assertEqual(
                (mount_dir / "home/admin/.openclaw/.env").read_text(encoding="utf-8"),
                "OPENAI_API_KEY=sk-secret\n",
            )
            openclaw_config = json.loads(
                (mount_dir / "home/admin/.openclaw/openclaw.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                openclaw_config["agents"]["defaults"]["model"]["primary"],
                "openai/gpt-5.4",
            )
            self.assertEqual(openclaw_config["tools"]["profile"], "coding")
            self.assertEqual(openclaw_config["commands"]["native"], "auto")
            self.assertEqual(openclaw_config["session"]["dmScope"], "per-channel-peer")
            self.assertEqual(openclaw_config["plugins"]["entries"]["slack"]["enabled"], True)
            self.assertEqual(openclaw_config["channels"]["slack"]["enabled"], True)
            self.assertEqual(openclaw_config["channels"]["slack"]["botToken"], "xoxb-test")
            self.assertEqual(openclaw_config["gateway"]["auth"]["mode"], "token")
            self.assertTrue(openclaw_config["gateway"]["auth"]["token"])
            auth_profiles = json.loads(
                (
                    mount_dir / "home/admin/.openclaw/agents/main/agent/auth-profiles.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                auth_profiles["profiles"]["openai:default"]["keyRef"]["id"],
                "OPENAI_API_KEY",
            )
            slack_allow_from = json.loads(
                (
                    mount_dir / "home/admin/.openclaw/credentials/slack-default-allowFrom.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(slack_allow_from["allowFrom"], ["U123"])
            self.assertEqual(
                oct((mount_dir / "home/admin/.openclaw").stat().st_mode & 0o777),
                "0o700",
            )
            self.assertEqual(
                oct((mount_dir / "home/admin/.openclaw/credentials").stat().st_mode & 0o777),
                "0o700",
            )
            self.assertTrue(
                (mount_dir / "home/admin/.config/systemd/user/openclaw-gateway.service").exists()
            )
            self.assertTrue(
                (
                    mount_dir
                    / "home/admin/.config/systemd/user/default.target.wants/openclaw-gateway.service"
                ).is_symlink()
            )
            self.assertTrue((mount_dir / "var/lib/systemd/linger/admin").exists())
            self.assertTrue(
                (mount_dir / "usr/local/sbin/openclaw-activation-refresh.sh").exists()
            )
            self.assertTrue(
                (mount_dir / "etc/systemd/system/openclaw-activation-refresh.service").exists()
            )
            self.assertTrue(
                (
                    mount_dir
                    / "etc/systemd/system/multi-user.target.wants/openclaw-activation-refresh.service"
                ).is_symlink()
            )
            self.assertTrue(
                (mount_dir / "var/lib/openclaw/bootstrap/pending-gateway-restart").exists()
            )
            shared_access_config = json.loads(
                (mount_dir / "home/admin/.openclaw/shared-access.json").read_text(encoding="utf-8")
            )
            self.assertTrue(shared_access_config["opt_in"])
            self.assertEqual(shared_access_config["slack_user_id"], "UOWNER")
            self.assertEqual(
                shared_access_config["email_intro_lookup"]["command"][2],
                "{request_path}",
            )
            self.assertEqual(shared_access_config["capabilities"], ["email_intro_lookup"])
            self.assertEqual(
                shared_access_config["email_intro_lookup"]["allowedRecipientFilters"],
                [
                    "leads@tribecap.co",
                    "portfolio-passive@tribecap.co",
                    "portfolio-active@tribecap.co",
                    "crypto-passive@tribecap.co",
                    "crypto@tribecap.co",
                ],
            )
            self.assertTrue((mount_dir / "usr/local/bin/openclaw-shared-access").exists())
            self.assertTrue((mount_dir / "usr/local/bin/company-email-intro-lookup").exists())
            self.assertFalse((mount_dir / "usr/local/bin/company-email-intro-draft").exists())
            self.assertTrue((mount_dir / "usr/local/bin/pkg-install").exists())
            self.assertTrue((mount_dir / "usr/local/bin/pkg-search").exists())
            self.assertTrue((mount_dir / "usr/local/sbin/openclaw-apt-install-root").exists())
            self.assertEqual(
                (
                    mount_dir / "etc/sudoers.d/openclaw-package-install"
                ).read_text(encoding="utf-8").strip(),
                "admin ALL=(root) NOPASSWD: /usr/local/sbin/openclaw-apt-install-root",
            )

    def test_activate_user_persists_new_manifest_for_reuse(self) -> None:
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
            (mount_dir / "etc").mkdir(parents=True, exist_ok=True)
            (mount_dir / "etc/passwd").write_text(
                "admin:x:1000:1000::/home/admin:/bin/bash\n",
                encoding="utf-8",
            )
            (mount_dir / "home/admin").mkdir(parents=True, exist_ok=True)
            manifest_path = storage_root / "incoming-manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "profile": "prod",
                        "openclaw": {
                            "env": {
                                "OPENAI_API_KEY": "prod",
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            @contextlib.contextmanager
            def fake_mount(_image: Path, _mount_base: Path, _mount_name: str):
                yield mount_dir

            with (
                mock.patch("openclaw_hostctl.hostctl.require_root"),
                mock.patch("openclaw_hostctl.hostctl.mounted_image", fake_mount),
                mock.patch("openclaw_hostctl.hostctl.chown_tree"),
                mock.patch("openclaw_hostctl.hostctl.os.chown"),
                mock.patch.object(HostController, "is_running", return_value=False),
            ):
                controller.activate_user(
                    "alice",
                    manifest_path=manifest_path,
                    force=False,
                    restart=False,
                )

            self.assertEqual(
                config.user_config_store_path("alice").read_text(encoding="utf-8"),
                manifest_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                oct(config.user_config_store_path("alice").stat().st_mode & 0o777),
                "0o600",
            )

    def test_sync_coordinator_directory_from_manifest_uses_slack_member_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage_root = Path(tmp_dir)
            config = example_config(storage_root)
            controller = HostController(config)
            user = UserRecord(
                user_id="francis",
                display_name="Francis Zhan",
                machine_name="openclaw-francis",
                ip_address="172.31.0.11",
                mac_address="06:00:ac:1f:00:0b",
                tap_name="ocfrancis",
                rootfs_path=str(config.user_rootfs_path("francis")),
                created_at="2026-03-10T00:00:00Z",
            )
            coordinator_config_path = storage_root / "coordinator-config.json"
            coordinator_state_root = storage_root / "coordinator-state"
            coordinator_config_path.write_text(
                json.dumps(
                    {
                        "state_root": str(coordinator_state_root),
                        "relay_command": [
                            "openclaw-hostctl",
                            "shared-access",
                            "execute",
                            "{owner_vm_user_id}",
                        ],
                        "request_timeout_seconds": 180,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            controller._sync_coordinator_directory_from_manifest(
                user,
                {
                    "user_id": "francis",
                    "slack_user_id": "U0275195STW",
                    "shared_access": {
                        "opt_in": True,
                        "capabilities": ["email_intro_lookup"],
                    },
                },
                coordinator_config_path=coordinator_config_path,
            )

            directory = json.loads((coordinator_state_root / "directory.json").read_text(encoding="utf-8"))
            self.assertEqual(directory["U0275195STW"]["vm_user_id"], "francis")
            self.assertEqual(directory["U0275195STW"]["display_name"], "Francis Zhan")
            self.assertEqual(directory["U0275195STW"]["vm_address"], "172.31.0.11")
            self.assertTrue(directory["U0275195STW"]["opt_in"])


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
