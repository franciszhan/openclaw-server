from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator


def run(cmd: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def copy_reflink(source: Path, destination: Path) -> None:
    run(["cp", "--reflink=always", str(source), str(destination)])


def copy_full(source: Path, destination: Path) -> None:
    run(["cp", "--sparse=always", str(source), str(destination)])


def clone_disk(source: Path, destination: Path, *, mode: str) -> None:
    if mode == "reflink":
        copy_reflink(source, destination)
        return
    if mode == "copy":
        copy_full(source, destination)
        return
    raise ValueError(f"unsupported storage_copy_mode '{mode}'")


def ensure_reflink_supported(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    source = path / ".reflink-check-src"
    destination = path / ".reflink-check-dst"
    try:
        source.write_text("openclaw\n", encoding="utf-8")
        copy_reflink(source, destination)
    finally:
        source.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)


def extend_ext4_image(image_path: Path, size_gib: int) -> None:
    run(["truncate", "-s", f"{size_gib}G", str(image_path)])
    run(["e2fsck", "-pf", str(image_path)])
    run(["resize2fs", str(image_path)])


@contextlib.contextmanager
def mounted_image(image_path: Path, mount_base: Path, mount_name: str) -> Iterator[Path]:
    mount_dir = mount_base / mount_name
    mount_dir.mkdir(parents=True, exist_ok=True)
    mounted = False
    try:
        run(["mount", "-o", "loop", str(image_path), str(mount_dir)])
        mounted = True
        yield mount_dir
    finally:
        if mounted:
            subprocess.run(["umount", str(mount_dir)], check=False, text=True)
        shutil.rmtree(mount_dir, ignore_errors=True)


def write_file(root: Path, relative_path: str, content: str, mode: int) -> None:
    target = root / relative_path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    os.chmod(target, mode)


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def lookup_guest_user(root: Path, username: str) -> tuple[int, int]:
    passwd_path = root / "etc/passwd"
    for line in passwd_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[0] == username:
            return int(parts[2]), int(parts[3])
    raise ValueError(f"guest image is missing user '{username}'")


def chown_tree(path: Path, uid: int, gid: int) -> None:
    for current_root, dirnames, filenames in os.walk(path):
        os.chown(current_root, uid, gid)
        for dirname in dirnames:
            os.chown(Path(current_root) / dirname, uid, gid)
        for filename in filenames:
            os.chown(Path(current_root) / filename, uid, gid)


def sanitize_snapshot_label(label: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")
    if not cleaned:
        raise ValueError("snapshot label must contain at least one alphanumeric character")
    return cleaned
