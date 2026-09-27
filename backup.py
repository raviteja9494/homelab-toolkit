#!/usr/bin/env python3
"""
Minimal homelab backup — single file, stdlib only. Edit settings below.

Usage:
  sudo python3 backup.py init
  sudo python3 backup.py run
  sudo python3 backup.py run --skip-immich
  sudo python3 backup.py run --dry-run    # immich preview only (borg --dry-run)

Get USB UUID (plug drive in first):
  lsblk -f
  # or:  sudo blkid /dev/sdX1

init does NOT format the drive. Format a new USB once (erases everything):
  sudo umount /mnt/usb_backup 2>/dev/null || true
  sudo mkfs.ext4 -F -L homelab-backup /dev/sdX1
  # put UUID from blkid into USB_UUID below

If the wrong drive is left mounted at MOUNT_POINT, the script unmounts it
and mounts the USB matching USB_UUID automatically.

USB layout:

  /mnt/usb_backup/
    backup.info   <- drive label (created by init, updated each run)
    homelab-backup/
      immich-borg/  <- Immich photos (Borg repo — encrypted, deduplicated)
      pve/          <- host config (/etc/pve, network, fstab, /root, homelab-toolkit)
      logs/         <- one .log file per run
      vzdump/       <- VM + LXC archives

rsync flags (all rsync jobs use the same set):
  -a            archive mode (perms, times, symlinks, recursion)
  -H            preserve hard links
  -A            preserve ACLs
  -X            preserve extended attributes
  --numeric-ids  keep raw uid/gid (no name lookups)
  --info=progress2  one updating progress line for the whole transfer
  --delete       mirror source — files removed on host are removed on USB

Immich uses Borg instead of rsync — every run is a new dated snapshot, kept
per BORG_KEEP_DAILY/WEEKLY/MONTHLY in settings.py, instead of one mirrored
copy. Requires BORG_PASSPHRASE set in settings.py (see comment there) and
the borg binary installed (apt install borgbackup).

List Immich snapshots:
  BORG_PASSPHRASE=... borg list /mnt/usb_backup/homelab-backup/immich-borg

Restore a whole library (run from the destination directory):
  cd /mnt/sata_thin_pool/immich_data
  BORG_PASSPHRASE=... borg extract /mnt/usb_backup/homelab-backup/immich-borg::<archive>
"""

from __future__ import annotations
from settings import *

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path



# -----------------------------------------------------------------------------

MP = Path(MOUNT_POINT)
ROOT = MP / BACKUP_ROOT
INFO = MP / "backup.info"
LOG_DIR = ROOT / "logs"
BORG_REPO = ROOT / "immich-borg"
DUMP_SUFFIXES = (".tar.zst", ".vma.zst")
RSYNC = ["rsync", "-aHAX", "--numeric-ids", "--info=progress2", "--delete"]
BACKUP_INFO_ORDER = (
    "Backup Name", "Version", "Created", "Filesystem", "Host", "UUID",
    "Toolkit Version", "Git Commit",
    "Proxmox Version", "Kernel", "Hostname",
    "Backup Type",
    "Last Backup", "Status", "Duration",
    "Host Config", "Immich", "Vzdump", "Logs", "Total Size", "Free Space",
)
_log: Path | None = None


def die(msg: str) -> None:
    note(f"ERROR: {msg}")
    sys.exit(1)


def note(msg: str) -> None:
    print(msg)
    if _log:
        with _log.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def run(
    cmd: list[str],
    *,
    check: bool = True,
    stream: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    note(f"  $ {' '.join(cmd)}")
    if stream:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            if _log:
                with _log.open("a", encoding="utf-8") as f:
                    f.write(line)
        result = subprocess.CompletedProcess(cmd, proc.wait(), "", "")
        if check and result.returncode:
            die(f"command failed ({result.returncode})")
        return result

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and result.returncode:
        die(result.stderr or result.stdout or f"exit {result.returncode}")
    return result


def flush_disk() -> None:
    """Push pending writes to the USB before unmount (os.sync + sync)."""
    note("flushing disk buffers")
    if hasattr(os, "sync"):
        os.sync()
    run(["sync"])


def need_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        die("run as root")


def format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration (e.g. 1h 45m 26s)."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {sec}s"


def get_proxmox_info() -> dict[str, str]:
    """Collect Proxmox host metadata; never raises."""
    info = {
        "Proxmox Version": "Unknown",
        "Kernel": "Unknown",
        "Hostname": HOSTNAME,
    }
    try:
        info["Kernel"] = platform.release()
    except Exception:
        pass
    try:
        result = run(["pveversion"], check=False)
        if result.returncode == 0 and result.stdout.strip():
            info["Proxmox Version"] = result.stdout.strip()
    except Exception:
        pass
    return info


def get_git_commit(toolkit_dir: Path) -> str:
    """Return short git hash for toolkit_dir, or Unknown if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=toolkit_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "Unknown"


def determine_backup_type(*, dry_run: bool, skip_immich: bool) -> str:
    """Return backup type label for backup.info."""
    if dry_run:
        return "Dry Run"
    if skip_immich or SKIP_IMMICH:
        return "System Only"
    return "Full"


def collect_folder_sizes() -> dict[str, str]:
    """Return du -sh sizes keyed by backup.info field names."""
    labels = {
        "pve": "Host Config",
        "immich-borg": "Immich",
        "vzdump": "Vzdump",
        "logs": "Logs",
    }
    sizes: dict[str, str] = {}
    for folder, label in labels.items():
        path = ROOT / folder
        if not path.exists():
            continue
        out = run(["du", "-sh", str(path)], check=False).stdout.strip()
        if out:
            sizes[label] = out.split(maxsplit=1)[0]
    total = run(["du", "-sh", str(ROOT)], check=False).stdout.strip()
    if total:
        sizes["Total Size"] = total.split(maxsplit=1)[0]
    free_gib = shutil.disk_usage(MP).free / (1024**3)
    sizes["Free Space"] = f"{free_gib:.1f}G"
    return sizes


def all_vmids() -> list[int]:
    raw = run(
        ["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"]
    ).stdout
    return sorted(
        int(item["vmid"])
        for item in json.loads(raw)
        if not item.get("template")
    )


def vmids_to_backup() -> list[int]:
    ids = VMIDS if VMIDS else all_vmids()
    if not ids:
        die("no VMs or LXCs found — set VMIDS manually or create guests first")
    if not VMIDS:
        note(f"  auto VMIDs (no templates): {ids}")
    return ids


def is_mounted() -> bool:
    """True only if MOUNT_POINT itself is a mount point (not merely inside one)."""
    return run(["findmnt", "--mountpoint", str(MP), "--noheadings"], check=False).returncode == 0


def mounted_uuid_fstype() -> tuple[str, str] | None:
    if not is_mounted():
        return None
    parts = run(
        ["findmnt", "--mountpoint", str(MP), "-o", "UUID,FSTYPE", "--noheadings"]
    ).stdout.split()
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def mount_usb() -> None:
    MP.mkdir(parents=True, exist_ok=True)
    if not (Path("/dev/disk/by-uuid") / USB_UUID).exists():
        die(f"backup USB not connected — UUID not found: {USB_UUID}")

    info = mounted_uuid_fstype()
    if info and info[0] == USB_UUID:
        if info[1] != "ext4":
            die(f"expected ext4, found {info[1]}")
        return

    if info and info[0] != USB_UUID:
        note(f"unmounting wrong drive at {MP} (UUID {info[0]})")
        run(["umount", str(MP)])

    run(["mount", "-t", "ext4", f"UUID={USB_UUID}", str(MP)])

    info = mounted_uuid_fstype()
    if not info or info[0] != USB_UUID:
        die(f"failed to mount USB {USB_UUID} at {MP}")
    if info[1] != "ext4":
        die(f"expected ext4, found {info[1]}")


def unmount_usb() -> None:
    if is_mounted():
        flush_disk()
        run(["umount", str(MP)])


def check_disk_space() -> None:
    free_gib = shutil.disk_usage(MP).free / (1024**3)
    note(f"USB free space: {free_gib:.1f} GiB")
    if free_gib < MIN_FREE_GIB:
        die(f"USB low on space — {free_gib:.1f} GiB free, need {MIN_FREE_GIB} GiB")


def start_log(tag: str) -> Path:
    global _log
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{tag}.log"
    _log = path
    note(f"=== {tag} started ===")
    return path


def prune_old_logs() -> None:
    logs = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[LOG_KEEP:]:
        old.unlink(missing_ok=True)


def read_backup_info(path: Path = INFO) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return info


def write_backup_info(info: dict[str, str], path: Path = INFO) -> None:
    path.write_text(
        "".join(f"{k}: {info[k]}\n" for k in BACKUP_INFO_ORDER if k in info),
        encoding="utf-8",
    )


def init_backup_info() -> None:
    if INFO.exists():
        return
    write_backup_info({
        "Backup Name": "homelab-backup",
        "Version": "1",
        "Created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "Filesystem": "ext4",
        "Host": HOSTNAME,
        "UUID": USB_UUID,
        "Toolkit Version": SCRIPT_VERSION,
        "Git Commit": get_git_commit(Path(TOOLKIT_PATH)),
        "Hostname": HOSTNAME,
        "Last Backup": "Never",
        "Status": "Initialized",
    })


def verify_backup_info() -> None:
    if not INFO.is_file():
        die("backup.info missing — run: simple_backup.py init")
    info = read_backup_info()
    if info.get("UUID") != USB_UUID:
        die("backup.info UUID does not match USB_UUID in this script")


def update_backup_info(
    *,
    status: str,
    elapsed: float,
    backup_type: str,
    sizes: dict[str, str],
    proxmox_info: dict[str, str],
    git_commit: str,
) -> None:
    """Update backup.info after a successful backup run."""
    info = read_backup_info() if INFO.exists() else {}
    info.update({
        "Backup Name": info.get("Backup Name", "homelab-backup"),
        "Version": info.get("Version", "1"),
        "Filesystem": "ext4",
        "Host": HOSTNAME,
        "UUID": USB_UUID,
        "Toolkit Version": SCRIPT_VERSION,
        "Git Commit": git_commit,
        "Hostname": proxmox_info.get("Hostname", HOSTNAME),
        "Proxmox Version": proxmox_info.get("Proxmox Version", "Unknown"),
        "Kernel": proxmox_info.get("Kernel", "Unknown"),
        "Backup Type": backup_type,
        "Last Backup": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Status": status,
        "Duration": format_duration(elapsed),
    })
    info.update(sizes)
    if "Created" not in info:
        info["Created"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_backup_info(info)


def rsync(src: Path, dst: Path, *, dry_run: bool = False) -> None:
    if not src.exists():
        die(f"missing: {src}")
    cmd = RSYNC + (["-n"] if dry_run else [])
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        cmd += [f"{src}/", str(dst)]
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd += [str(src), str(dst)]
    run(cmd, stream=True)


def need_borg() -> None:
    if shutil.which("borg") is None:
        die("borg not found — install with: apt install borgbackup")


def borg_env() -> dict[str, str]:
    if not BORG_PASSPHRASE:
        die("BORG_PASSPHRASE is not set in settings.py")
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = BORG_PASSPHRASE
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    return env


def borg_repo_initialized() -> bool:
    return (BORG_REPO / "config").is_file()


def borg_init_repo() -> None:
    need_borg()
    BORG_REPO.parent.mkdir(parents=True, exist_ok=True)
    if borg_repo_initialized():
        return
    note(f"initializing borg repo: {BORG_REPO}")
    run(["borg", "init", "--encryption=repokey-blake2", str(BORG_REPO)], env=borg_env())


def borg_backup_immich(*, dry_run: bool = False) -> None:
    src = Path(IMMICH_PATH)
    if not src.exists():
        die(f"missing: {src}")
    borg_init_repo()

    archive = f"{BORG_REPO}::{HOSTNAME}-{{now:%Y-%m-%d_%H-%M-%S}}"
    if dry_run:
        # borg's --dry-run does a real scan against the last archive without
        # writing anything — closest equivalent to rsync -n for this.
        run(["borg", "create", "--dry-run", "--list", "--stats", archive, str(src)],
            stream=True, env=borg_env())
        return

    run(["borg", "create", "--stats", "--compression", "lz4", archive, str(src)],
        stream=True, env=borg_env())
    run(
        ["borg", "prune",
         "--keep-daily", str(BORG_KEEP_DAILY),
         "--keep-weekly", str(BORG_KEEP_WEEKLY),
         "--keep-monthly", str(BORG_KEEP_MONTHLY),
         "--stats", str(BORG_REPO)],
        stream=True, env=borg_env(),
    )
    # prune only marks old data deletable; compact reclaims the actual space.
    run(["borg", "compact", str(BORG_REPO)], stream=True, env=borg_env())


def format_size_lines(sizes: dict[str, str]) -> list[str]:
    """Format collected sizes for console summary output."""
    display = (
        ("Host Config", "pve"),
        ("Immich", "immich-borg"),
        ("Vzdump", "vzdump"),
        ("Logs", "logs"),
        ("Total Size", "total"),
    )
    lines: list[str] = []
    for label, name in display:
        if label in sizes:
            lines.append(f"  {sizes[label]:>8}  {name}/")
    if "Free Space" in sizes:
        lines.append(f"  {sizes['Free Space']:>8}  free")
    return lines


def print_summary(
    *,
    elapsed: float,
    vmids: list[int],
    log_path: Path | None,
    sizes: dict[str, str],
) -> None:
    note("")
    note("=== backup summary ===")
    note(f"  duration : {format_duration(elapsed)}")
    note(f"  VMIDs    : {vmids or 'none'}")
    if "Free Space" in sizes:
        note(f"  USB free : {sizes['Free Space']}")
    if log_path:
        note(f"  log      : {log_path}")
    if INFO.exists():
        info = read_backup_info()
        note(f"  status   : {info.get('Status', '?')} ({info.get('Last Backup', '?')})")
        if info.get("Backup Type"):
            note(f"  type     : {info['Backup Type']}")
    size_lines = format_size_lines(sizes)
    if size_lines:
        note("  sizes    :")
        for line in size_lines:
            note(line)
    note("======================")


def prune_old_dumps(dump: Path, vmid: int) -> None:
    archives = sorted(
        [p for p in dump.glob(f"vzdump-*-{vmid}-*") if p.name.endswith(DUMP_SUFFIXES)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in archives[VZDUMP_KEEP_LAST:]:
        old.unlink(missing_ok=True)
        for suf in DUMP_SUFFIXES:
            if old.name.endswith(suf):
                old.with_name(old.name.removesuffix(suf) + ".log").unlink(missing_ok=True)


def cmd_init() -> None:
    need_root()
    mount_usb()
    try:
        start_log("init")
        for d in ("pve", "logs", "vzdump"):
            (ROOT / d).mkdir(parents=True, exist_ok=True)
        init_backup_info()
        borg_init_repo()
        flush_disk()
        note("OK — folders created (drive was not formatted)")
    finally:
        unmount_usb()


def cmd_run(*, dry_run: bool, skip_immich: bool) -> None:
    need_root()
    mount_usb()
    started = time.monotonic()
    log_path: Path | None = None
    backed_up: list[int] = []

    try:
        if not (ROOT / "pve").is_dir():
            die("not initialized — run: backup.py init")
        verify_backup_info()

        log_path = start_log("dry-run" if dry_run else "run")
        check_disk_space()

        if dry_run:
            if skip_immich:
                die("--dry-run only applies to immich backup")
            borg_backup_immich(dry_run=True)
            note("OK — immich dry run done")
            return

        pve = ROOT / "pve"
        note("--- host config ---")
        rsync(Path("/etc/pve"), pve / "etc-pve")
        rsync(Path("/etc/network"), pve / "etc-network")
        rsync(Path("/etc/fstab"), pve / "fstab")
        rsync(Path("/root"), pve / "root")
        toolkit = Path(TOOLKIT_PATH)
        if toolkit.exists():
            rsync(toolkit, pve / "homelab-toolkit")
        else:
            note(f"  skip homelab-toolkit — not found: {toolkit}")

        dump = ROOT / "vzdump"
        dump.mkdir(parents=True, exist_ok=True)
        note("--- VMs / LXCs ---")
        for vmid in vmids_to_backup():
            run(
                ["vzdump", str(vmid), "--dumpdir", str(dump), "--mode", "snapshot", "--compress", "zstd"],
                stream=True,
            )
            prune_old_dumps(dump, vmid)
            backed_up.append(vmid)

        if not skip_immich and not SKIP_IMMICH:
            note("--- immich (borg) ---")
            borg_backup_immich()
        else:
            note("--- immich skipped ---")

        prune_old_logs()
        elapsed = time.monotonic() - started
        sizes = collect_folder_sizes()
        update_backup_info(
            status="Success",
            elapsed=elapsed,
            backup_type=determine_backup_type(dry_run=False, skip_immich=skip_immich),
            sizes=sizes,
            proxmox_info=get_proxmox_info(),
            git_commit=get_git_commit(Path(TOOLKIT_PATH)),
        )
        print_summary(elapsed=elapsed, vmids=backed_up, log_path=log_path, sizes=sizes)
        flush_disk()
        note("OK — backup done")
    finally:
        unmount_usb()


def main() -> None:
    p = argparse.ArgumentParser(description="minimal homelab backup")
    p.add_argument("cmd", choices=["init", "run"])
    p.add_argument("--dry-run", action="store_true", help="immich preview only (borg --dry-run)")
    p.add_argument("--skip-immich", action="store_true", help="skip immich backup")
    a = p.parse_args()
    if a.cmd == "init":
        cmd_init()
    else:
        cmd_run(dry_run=a.dry_run, skip_immich=a.skip_immich)


if __name__ == "__main__":
    main()
