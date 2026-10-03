# Homelab Backup Toolkit

Minimal Proxmox homelab backup script — single file, stdlib only.
Manual workflow: plug in the USB drive, run the script, wait for **OK**, unplug.

## Quick Start

```bash
# Edit settings (USB UUID, passphrase, paths)
nano settings.py

# Initialize the USB backup drive
sudo python3 backup.py init

# Run a full backup
sudo python3 backup.py run

# Dry run (Immich preview only, nothing is written)
sudo python3 backup.py run --dry-run

# Skip Immich photos on this run
sudo python3 backup.py run --skip-immich

# Verify the Borg repos (quick), or verify all data (slow)
sudo python3 backup.py check
sudo python3 backup.py check --deep
```

The drive is unmounted automatically at the end. Unplug it only after you see
`drive unmounted — safe to unplug`.

## Setup

### Get USB UUID
```bash
lsblk -f
# or:
sudo blkid /dev/sdX1
```

### Format a new USB drive (one-time, erases everything)
```bash
sudo umount /mnt/usb_backup 2>/dev/null || true
sudo mkfs.ext4 -F -L homelab-backup /dev/sdX1
```

Put the UUID from `blkid` into `USB_UUID` in `settings.py`.

## Configuration

`settings.py` is split into five sections:

1. **Backup drive** — USB UUID, mount point, backup root folder
2. **What gets backed up** — every source path in one place: Immich, KeePass,
   toolkit, plus the VM/LXC selection (empty = all non-template guests)
3. **Borg encryption** — `BORG_PASSPHRASE` (required)
4. **Retention** — Immich/KeePass snapshot history, vzdump archives per guest, logs
5. **Options** — skip flags and the minimum free space requirement

## Backup Layout

```
/mnt/usb_backup/
├── backup.info          # Drive metadata and backup status
└── homelab-backup/
    ├── immich-borg/     # Immich photos (Borg repo: encrypted, deduplicated)
    ├── keepass-borg/    # KeePass DBs (Borg repo, long retention)
    ├── pve/             # Host config (/etc/pve, network, fstab, /root, toolkit)
    ├── vzdump/          # VM and LXC archives
    └── logs/            # One .log file per run
```

## What happens in a run

1. KeePass (Borg) — first, because it is tiny and the most critical
2. Host config (rsync mirror; `/root` without `.cache/`)
3. VMs and LXCs (vzdump)
4. Immich (Borg, everything under `IMMICH_PATH`)

## Safety

- **Wrong drive**: if something else is mounted at the mount point, the script
  stops and never unmounts it
- **Config check**: empty `USB_UUID` or `BORG_PASSPHRASE`, or missing `borg`,
  fails before anything is mounted
- **Empty sources are refused**: an unmounted `/etc/pve` or data disk would
  otherwise wipe the rsync mirror (`--delete`) or let Borg prune replace good
  snapshots with empty ones
- **Warnings are not failures**: Borg exit 1 (file changed while reading) and
  rsync exit 24 (files vanished) are tolerated; anything else aborts
- **Honest status**: `backup.info` shows `Running` during a run, then
  `Success` or `Failed`. `Last Backup` only changes on success
- **Disk space check**: aborts if the drive has less than `MIN_FREE_GIB` free
- **Logs**: command output goes to the console and to a log file on the drive

## Restore

### Restore a VM
```bash
qmrestore <archive> <vmid>
```

### Restore an LXC
```bash
pct restore <vmid> <archive>
```

### Restore Immich or KeePass (Borg)

List snapshots:
```bash
BORG_PASSPHRASE=... borg list /mnt/usb_backup/homelab-backup/immich-borg
```

Borg stores paths without the leading `/`, so extract from `/` to restore in
place:
```bash
cd /
BORG_PASSPHRASE=... borg extract /mnt/usb_backup/homelab-backup/immich-borg::<archive>
```

To restore into a different directory, strip the 3 leading path components
(`mnt/sata_thin_pool/immich_data`; adjust if your path has a different depth):
```bash
cd /some/other/dir
BORG_PASSPHRASE=... borg extract --strip-components 3 \
    /mnt/usb_backup/homelab-backup/immich-borg::<archive>
```

The same applies to `keepass-borg`. Try a restore into a scratch directory
once, before you ever need it.

### Restore host config
`pve/` holds plain copies. Copy individual files back as needed. Do not
`rsync --delete` back into `/etc/pve` while Proxmox is running, because it is
a live cluster filesystem.

## Dependencies

- Linux with Proxmox
- Python 3.9+
- `borg` >= 1.2 (`apt install borgbackup`)
- `rsync`, `du`, `sync`, `umount`, `mount`, `findmnt`
- Must run as `root`