# =============================================================================
# 1. Backup drive
# =============================================================================

USB_UUID = ""                    # required — find with: lsblk -f
MOUNT_POINT = "/mnt/usb_backup"
BACKUP_ROOT = "homelab-backup"   # folder created on the drive

# =============================================================================
# 2. What gets backed up (all source paths live here)
# =============================================================================
# Always backed up (fixed in backup.py): /etc/pve, /etc/network, /etc/fstab, /root

HOSTNAME = "pve"                                    # used in Borg archive names
IMMICH_PATH = "/mnt/sata_thin_pool/immich_data"     # Borg repo "immich-borg"
KEEPASS_PATH = "/mnt/sata_thin_pool/keepass_database"    # Borg repo "keepass-borg"
TOOLKIT_PATH = "/opt/homelab-toolkit"               # mirrored with rsync
VMIDS = []                                          # empty = every VM/LXC (templates excluded)

# =============================================================================
# 3. Borg encryption (shared by the Immich and KeePass repos)
# =============================================================================
# This file is mirrored to the USB with the rest of the toolkit, so the
# passphrase lives on the drive too (fine for a single-user homelab).
# Also keep a copy somewhere else (e.g. your password manager): if the drive's
# copy is ever unreadable and you have no other copy, every Borg repo is
# permanently locked.

BORG_PASSPHRASE = ""             # required — set before the first run

# =============================================================================
# 4. Retention (how much history to keep)
# =============================================================================

IMMICH_KEEP_DAILY = 7
IMMICH_KEEP_WEEKLY = 4
IMMICH_KEEP_MONTHLY = 6

KEEPASS_KEEP_DAILY = 30          # tiny data, so long history costs almost nothing
KEEPASS_KEEP_WEEKLY = 12
KEEPASS_KEEP_MONTHLY = 24

VZDUMP_KEEP_LAST = 3             # VM/LXC archives kept per guest
LOG_KEEP = 10                    # run logs kept on the drive

# =============================================================================
# 5. Options
# =============================================================================

SKIP_IMMICH = False
SKIP_KEEPASS = False
MIN_FREE_GIB = 20                # abort if the drive has less free space than this