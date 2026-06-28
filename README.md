# Homelab Backup Toolkit

Minimal Proxmox homelab backup script — single file, stdlib only.

## Quick Start

```bash
# Edit settings (USB UUID, paths, VM IDs)
nano settings.py

# Initialize the USB backup drive
sudo python3 backup.py init

# Run a full backup
sudo python3 backup.py run

# Dry run (immich rsync preview only)
sudo python3 backup.py run --dry-run

# Skip Immich photos on this run
sudo python3 backup.py run --skip-immich
```

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

Edit `settings.py` to customize:
- USB drive UUID and mount point
- Backup root directory
- Host paths (Proxmox, Immich)
- VM/LXC selection (empty = all non-template guests)
- Retention policy (keep last N backups/logs)
- Minimum free space requirement

## Backup Layout

```
/mnt/usb_backup/
├── backup.info          # Drive metadata and backup status
└── homelab-backup/
    ├── pve/             # Host config (/etc/pve, network, fstab, /root, toolkit)
    ├── vzdump/          # VM and LXC archives
    ├── immich/          # Immich photos
    └── logs/            # One .log file per run
```

## Features

- **Safe mount logic**: Detects and unmounts wrong drives automatically
- **Disk space checks**: Aborts if USB has less than `MIN_FREE_GIB` free
- **Archive mode rsync**: Preserves permissions, times, ACLs, hard links
- **Backup metadata**: `backup.info` tracks version, timestamps, sizes, status
- **Retention**: Auto-prune old dumps and logs based on settings
- **Streaming logs**: Command output streamed to both console and log file
- **Graceful cleanup**: Flushes disk buffers and unmounts safely on exit

## Restore

### Restore a VM
```bash
qmrestore <archive> <vmid>
```

### Restore an LXC
```bash
pct restore <vmid> <archive>
```

### Restore Immich
```bash
rsync -aHAX backup/immich/ /mnt/sata_thin_pool/immich_data/
```

## Dependencies

- Linux with Proxmox
- Python 3.6+
- `rsync`, `du`, `sync`, `umount`, `mount`, `findmnt`
- Must run as `root`