#!/usr/bin/env python3
"""
Minimal homelab backup — single file, stdlib only. Edit settings.py.

Manual workflow: plug in the USB drive -> run this -> wait for "OK" -> unplug.

Usage:
  sudo python3 backup.py init
  sudo python3 backup.py run
  sudo python3 backup.py run --skip-immich
  sudo python3 backup.py run --dry-run    # immich preview only (borg --dry-run)
  sudo python3 backup.py check            # quick borg integrity check (both repos)
  sudo python3 backup.py check --deep     # full data verification (slow)

Get USB UUID (plug drive in first):
  lsblk -f
  # or:  sudo blkid /dev/sdX1

init does NOT format the drive. Format a new USB once (erases everything):
  sudo umount /mnt/usb_backup 2>/dev/null || true
  sudo mkfs.ext4 -F -L homelab-backup /dev/sdX1
  # put UUID from blkid into USB_UUID in settings.py

If a different drive is mounted at MOUNT_POINT the script stops (it never
unmounts anything it did not mount for this run).

USB layout:

  /mnt/usb_backup/
    backup.info   <- drive label + last run status (created by init)
    homelab-backup/
      immich-borg/   <- Immich photos (Borg repo — encrypted, deduplicated)
      keepass-borg/  <- KeePass DBs (Borg repo — separate, long retention)
      pve/           <- host config (/etc/pve, network, fstab, /root, homelab-toolkit)
      vzdump/        <- VM + LXC archives
      logs/          <- one .log file per run

Order of a run: KeePass -> host config -> VMs/LXCs -> Immich.

Safety rules built in:
  - rsync uses --delete (mirror), so empty sources are refused: an unmounted
    /etc/pve or disk would otherwise wipe the backup copy.
  - Borg sources must be non-empty too, otherwise prune would slowly replace
    good snapshots with empty ones.
  - Borg exit 1 (warnings, e.g. file changed while reading) and rsync exit 24
    (files vanished) are tolerated; anything else aborts the run.
  - backup.info Status is "Running" during a run, then "Success" or "Failed".
    Last Backup only changes on success.

rsync flags (all rsync jobs):
  -a  archive   -H hard links   -A ACLs   -X xattrs   --numeric-ids
  --delete       mirror source — files removed on host are removed on USB
  /root is synced without .cache/ (Borg's local cache lives there).

Immich and KeePass use Borg — each has its own repo and retention
(IMMICH_KEEP_* / KEEPASS_KEEP_* in settings.py). Needs borg >= 1.2
(apt install borgbackup) and BORG_PASSPHRASE in settings.py.

List snapshots:
  BORG_PASSPHRASE=... borg list /mnt/usb_backup/homelab-backup/immich-borg
  BORG_PASSPHRASE=... borg list /mnt/usb_backup/homelab-backup/keepass-borg

Restore (Borg stores paths without the leading "/", so extract from "/"):
  cd /
  BORG_PASSPHRASE=... borg extract /mnt/usb_backup/homelab-backup/immich-borg::<archive>
Restore into a different directory instead:
  cd /some/other/dir
  BORG_PASSPHRASE=... borg extract --strip-components 3 \\
      /mnt/usb_backup/homelab-backup/immich-borg::<archive>
(3 = mnt / sata_thin_pool / immich_data; adjust if your path has a different depth.)
"""

from __future__ import annotations
from settings import *

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn

VERSION = "1.4.1"

# -----------------------------------------------------------------------------
# Derived paths (from settings.py)
# -----------------------------------------------------------------------------

MP = Path(MOUNT_POINT)
ROOT = MP / BACKUP_ROOT
INFO = MP / "backup.info"
LOG_DIR = ROOT / "logs"
IMMICH_BORG_REPO = ROOT / "immich-borg"
KEEPASS_BORG_REPO = ROOT / "keepass-borg"

# -----------------------------------------------------------------------------
# Tool behaviour
# -----------------------------------------------------------------------------

DUMP_SUFFIXES = (".tar.zst", ".vma.zst")
RSYNC = ["rsync", "-aHAX", "--numeric-ids", "--delete", "--info=stats1"]
RSYNC_OK = (0, 24)  # 24 = some source files vanished during transfer
BORG_OK = (0, 1)    # 1 = warnings (e.g. file changed while reading)
BACKUP_INFO_ORDER = (
    "Backup Name", "Version", "Created", "UUID", "Toolkit Version",
    "Backup Type", "Last Backup", "Status", "Duration", "Last Check",
    "Host Config", "Immich", "KeePass", "Vzdump", "Logs", "Total Size", "Free Space",
)
_log: Path | None = None


# -----------------------------------------------------------------------------
# Logging and command execution
# -----------------------------------------------------------------------------

def die(msg: str) -> NoReturn:
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
    ok: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    """Run a command. Exit codes in `ok` count as success when check=True."""
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
        log = _log.open("a", encoding="utf-8") if _log else contextlib.nullcontext()
        with log as f:
            for line in proc.stdout:
                sys.stdout.write(line)
                if f:
                    f.write(line)
                    f.flush()
        result = subprocess.CompletedProcess(cmd, proc.wait(), "", "")
        detail = ""
    else:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        detail = result.stderr or result.stdout

    rc = result.returncode
    if check and rc not in ok:
        die(detail.strip() or f"command failed (exit {rc})")
    if rc and rc in ok:
        note(f"  warning: exit code {rc} (tolerated)")
    return result


def flush_disk() -> None:
    """Push pending writes to the USB before unmount."""
    note("flushing disk buffers")
    run(["sync"])


def need_root() -> None:
    if os.geteuid() != 0:
        die("run as root")


def check_config() -> None:
    """Fail early on unusable settings, before touching any mount."""
    if not USB_UUID.strip():
        die("USB_UUID is empty in settings.py — find it with: lsblk -f")
    if not BORG_PASSPHRASE:
        die("BORG_PASSPHRASE is empty in settings.py")
    if shutil.which("borg") is None:
        die("borg not found — install with: apt install borgbackup")


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


# -----------------------------------------------------------------------------
# USB drive
# -----------------------------------------------------------------------------

def is_mounted() -> bool:
    """True only if MOUNT_POINT itself is a mount point (not merely inside one)."""
    return run(["findmnt", "--mountpoint", str(MP), "--noheadings"], check=False).returncode == 0


def mount_usb() -> None:
    MP.mkdir(parents=True, exist_ok=True)
    if not (Path("/dev/disk/by-uuid") / USB_UUID).exists():
        die(f"backup USB not connected — UUID not found: {USB_UUID}")

    if is_mounted():
        found = run(
            ["findmnt", "--mountpoint", str(MP), "-o", "UUID", "--noheadings"]
        ).stdout.strip()
        if found == USB_UUID:
            return
        die(f"something else is mounted at {MP} (UUID: {found or 'unknown'}) — unmount it first")

    run(["mount", "-t", "ext4", f"UUID={USB_UUID}", str(MP)])
    if not is_mounted():
        die(f"failed to mount USB {USB_UUID} at {MP}")


def unmount_usb() -> None:
    global _log
    if is_mounted():
        flush_disk()
        run(["umount", str(MP)])
        _log = None  # the log file lived on the drive that is now unmounted
        note("drive unmounted — safe to unplug")


def check_disk_space() -> None:
    free_gib = shutil.disk_usage(MP).free / (1024**3)
    note(f"USB free space: {free_gib:.1f} GiB")
    if free_gib < MIN_FREE_GIB:
        die(f"USB low on space — {free_gib:.1f} GiB free, need {MIN_FREE_GIB} GiB")


# -----------------------------------------------------------------------------
# Run logs
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# backup.info
# -----------------------------------------------------------------------------

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
        "UUID": USB_UUID,
        "Toolkit Version": VERSION,
        "Last Backup": "Never",
        "Status": "Initialized",
    })


def verify_backup_info() -> None:
    if not INFO.is_file():
        die("backup.info missing — run: backup.py init")
    if read_backup_info().get("UUID") != USB_UUID:
        die("backup.info UUID does not match USB_UUID in settings.py")


def set_status(status: str) -> None:
    """Update only the Status line (used for Running / Failed)."""
    info = read_backup_info() if INFO.exists() else {}
    info["Status"] = status
    write_backup_info(info)


def update_backup_info(
    *,
    elapsed: float,
    backup_type: str,
    sizes: dict[str, str],
) -> None:
    """Record a successful run in backup.info."""
    info = read_backup_info() if INFO.exists() else {}
    info.update({
        "Backup Name": info.get("Backup Name", "homelab-backup"),
        "Version": info.get("Version", "1"),
        "UUID": USB_UUID,
        "Toolkit Version": VERSION,
        "Backup Type": backup_type,
        "Last Backup": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Status": "Success",
        "Duration": format_duration(elapsed),
    })
    info.update(sizes)
    info.setdefault("Created", datetime.now().astimezone().isoformat(timespec="seconds"))
    write_backup_info(info)


def collect_folder_sizes() -> dict[str, str]:
    """Return du -sh sizes keyed by backup.info field names."""
    labels = {
        "pve": "Host Config",
        "immich-borg": "Immich",
        "keepass-borg": "KeePass",
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


def format_size_lines(sizes: dict[str, str]) -> list[str]:
    """Format collected sizes for console summary output."""
    display = (
        ("Host Config", "pve"),
        ("Immich", "immich-borg"),
        ("KeePass", "keepass-borg"),
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


# -----------------------------------------------------------------------------
# Host config (rsync)
# -----------------------------------------------------------------------------

def require_nonempty(src: Path, why: str) -> None:
    if src.is_dir() and not any(src.iterdir()):
        die(f"{src} is empty — {why}. Is its disk/filesystem mounted?")


def rsync(src: Path, dst: Path, *, exclude: tuple[str, ...] = ()) -> None:
    if not src.exists():
        die(f"missing: {src}")
    require_nonempty(src, "refusing to mirror it, --delete would wipe the backup copy")
    cmd = RSYNC + [f"--exclude={e}" for e in exclude]
    if exclude:
        cmd.append("--delete-excluded")  # also clean previously copied excluded files
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        cmd += [f"{src}/", str(dst)]
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd += [str(src), str(dst)]
    run(cmd, stream=True, ok=RSYNC_OK)


# -----------------------------------------------------------------------------
# Borg (Immich, KeePass)
# -----------------------------------------------------------------------------

def borg_env() -> dict[str, str]:
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = BORG_PASSPHRASE
    env["BORG_RELOCATED_REPO_ACCESS_IS_OK"] = "yes"
    return env


def borg_repo_initialized(repo: Path) -> bool:
    return (repo / "config").is_file()


def borg_init_repo(repo: Path) -> None:
    repo.parent.mkdir(parents=True, exist_ok=True)
    if borg_repo_initialized(repo):
        return
    note(f"initializing borg repo: {repo}")
    run(["borg", "init", "--encryption=repokey-blake2", str(repo)], env=borg_env())


def borg_backup(
    label: str,
    src_path: str,
    repo: Path,
    *,
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
    dry_run: bool = False,
) -> None:
    src = Path(src_path)
    if not src.exists():
        die(f"missing: {src}")
    require_nonempty(src, "refusing to back it up, prune would eventually discard good snapshots")

    archive = f"{repo}::{HOSTNAME}-{{now:%Y-%m-%d_%H-%M-%S}}"
    if dry_run:
        if not borg_repo_initialized(repo):
            die(f"{label} repo not initialized — run: backup.py init")
        # borg's --dry-run reads the source without writing an archive —
        # closest equivalent to rsync -n.
        run(["borg", "create", "--dry-run", "--list", archive, str(src)],
            stream=True, env=borg_env(), ok=BORG_OK)
        return

    borg_init_repo(repo)
    note(f"--- {label} (borg) ---")
    run(["borg", "create", "--stats", "--compression", "lz4", archive, str(src)],
        stream=True, env=borg_env(), ok=BORG_OK)
    run(
        ["borg", "prune",
         "--keep-daily", str(keep_daily),
         "--keep-weekly", str(keep_weekly),
         "--keep-monthly", str(keep_monthly),
         "--stats", str(repo)],
        stream=True, env=borg_env(), ok=BORG_OK,
    )
    # prune only marks old data deletable; compact (borg >= 1.2) reclaims space.
    run(["borg", "compact", str(repo)], stream=True, env=borg_env())


# -----------------------------------------------------------------------------
# VMs and LXCs (vzdump)
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

def cmd_init() -> None:
    need_root()
    check_config()
    mount_usb()
    try:
        start_log("init")
        for d in ("pve", "logs", "vzdump"):
            (ROOT / d).mkdir(parents=True, exist_ok=True)
        init_backup_info()
        borg_init_repo(IMMICH_BORG_REPO)
        borg_init_repo(KEEPASS_BORG_REPO)
        flush_disk()
        note("OK — folders created (drive was not formatted)")
    finally:
        unmount_usb()


def cmd_run(*, dry_run: bool, skip_immich: bool) -> None:
    need_root()
    check_config()
    if dry_run and skip_immich:
        die("--dry-run only applies to immich backup")
    mount_usb()
    started = time.monotonic()
    log_path: Path | None = None
    backed_up: list[int] = []
    real_run = False   # True once backup.info says "Running"
    finished = False

    try:
        if not (ROOT / "pve").is_dir():
            die("not initialized — run: backup.py init")
        verify_backup_info()

        log_path = start_log("dry-run" if dry_run else "run")
        check_disk_space()

        skip_im = skip_immich or SKIP_IMMICH
        immich_args = dict(
            keep_daily=IMMICH_KEEP_DAILY, keep_weekly=IMMICH_KEEP_WEEKLY,
            keep_monthly=IMMICH_KEEP_MONTHLY,
        )

        if dry_run:
            borg_backup("immich", IMMICH_PATH, IMMICH_BORG_REPO, dry_run=True, **immich_args)
            note("OK — immich dry run done")
            return

        real_run = True
        set_status("Running")

        # KeePass first: tiny and most critical, so a failure further down
        # the list can never prevent it from being backed up.
        if not SKIP_KEEPASS:
            borg_backup(
                "keepass", KEEPASS_PATH, KEEPASS_BORG_REPO,
                keep_daily=KEEPASS_KEEP_DAILY, keep_weekly=KEEPASS_KEEP_WEEKLY,
                keep_monthly=KEEPASS_KEEP_MONTHLY,
            )
        else:
            note("--- keepass skipped ---")

        pve = ROOT / "pve"
        note("--- host config ---")
        rsync(Path("/etc/pve"), pve / "etc-pve")
        rsync(Path("/etc/network"), pve / "etc-network")
        rsync(Path("/etc/fstab"), pve / "fstab")
        rsync(Path("/root"), pve / "root", exclude=(".cache/",))
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

        if not skip_im:
            borg_backup("immich", IMMICH_PATH, IMMICH_BORG_REPO, **immich_args)
        else:
            note("--- immich skipped ---")

        skipped = [n for n, s in (("immich", skip_im), ("keepass", SKIP_KEEPASS)) if s]
        backup_type = "Full" if not skipped else f"Partial (skipped: {', '.join(skipped)})"

        prune_old_logs()
        elapsed = time.monotonic() - started
        sizes = collect_folder_sizes()
        update_backup_info(elapsed=elapsed, backup_type=backup_type, sizes=sizes)
        finished = True
        print_summary(elapsed=elapsed, vmids=backed_up, log_path=log_path, sizes=sizes)
        flush_disk()
        note("OK — backup done")
    finally:
        if real_run and not finished:
            try:
                set_status("Failed")
            except Exception:
                pass
        unmount_usb()


def cmd_check(*, deep: bool) -> None:
    """Verify Borg repo integrity. Quick = repository only; deep = verify all data."""
    need_root()
    check_config()
    mount_usb()
    try:
        verify_backup_info()
        start_log("check")
        flag = "--verify-data" if deep else "--repository-only"
        for label, repo in (("immich", IMMICH_BORG_REPO), ("keepass", KEEPASS_BORG_REPO)):
            if not borg_repo_initialized(repo):
                note(f"--- {label} check skipped — repo not found ---")
                continue
            note(f"--- {label} check ({'deep' if deep else 'quick'}) ---")
            run(["borg", "check", flag, str(repo)], stream=True, env=borg_env())
        info = read_backup_info()
        info["Last Check"] = f"{datetime.now():%Y-%m-%d %H:%M:%S} ({'deep' if deep else 'quick'})"
        write_backup_info(info)
        flush_disk()
        note("OK — borg repos verified")
    finally:
        unmount_usb()


def main() -> None:
    p = argparse.ArgumentParser(description="minimal homelab backup")
    p.add_argument("cmd", choices=["init", "run", "check"])
    p.add_argument("--dry-run", action="store_true", help="run: immich preview only (borg --dry-run)")
    p.add_argument("--skip-immich", action="store_true", help="run: skip immich backup")
    p.add_argument("--deep", action="store_true", help="check: verify all data (slow)")
    a = p.parse_args()
    if a.cmd != "run" and (a.dry_run or a.skip_immich):
        p.error("--dry-run and --skip-immich only apply to 'run'")
    if a.cmd != "check" and a.deep:
        p.error("--deep only applies to 'check'")
    if a.cmd == "init":
        cmd_init()
    elif a.cmd == "check":
        cmd_check(deep=a.deep)
    else:
        cmd_run(dry_run=a.dry_run, skip_immich=a.skip_immich)


if __name__ == "__main__":
    main()