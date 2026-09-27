# =============================================================================
# Backup Drive
# =============================================================================

USB_UUID = ""
MOUNT_POINT = "/mnt/usb_backup"
BACKUP_ROOT = "homelab-backup"

# =============================================================================
# Homelab
# =============================================================================

HOSTNAME = "pve"
IMMICH_PATH = "/mnt/sata_thin_pool/immich_data"
TOOLKIT_PATH = "/opt/homelab-toolkit"

# =============================================================================
# Immich Backup (Borg)
# =============================================================================
# Immich is backed up with Borg instead of rsync: deduplicated, encrypted,
# and versioned (multiple restore points instead of one mirrored copy).
#
# BORG_PASSPHRASE is required — the repo cannot be created or opened without
# it. Since it lives here in plain text, lock this file down:
#   chmod 600 settings.py
# Back this passphrase up somewhere OTHER than this USB drive — if it's lost,
# the Immich backups on the drive become permanently unreadable.

BORG_PASSPHRASE = ""             # set before first run
BORG_KEEP_DAILY = 7
BORG_KEEP_WEEKLY = 4
BORG_KEEP_MONTHLY = 6

# =============================================================================
# KeePass Backup (Borg)
# =============================================================================
# Own repo, own retention — tiny data, so keeping a lot of history costs
# almost nothing in space. Same BORG_PASSPHRASE is reused for this repo.

KEEPASS_PATH = "/mnt/sata_thin_pool/keepass_dbs"
KEEPASS_KEEP_DAILY = 30
KEEPASS_KEEP_WEEKLY = 12
KEEPASS_KEEP_MONTHLY = 24
SKIP_KEEPASS = False

# =============================================================================
# Backup Behaviour
# =============================================================================

VMIDS = []              # Empty = backup every VM/LXC
# Empty list = automatically backup every VM and LXC except templates.
# Populate with IDs to restrict backups.

VZDUMP_KEEP_LAST = 3
LOG_KEEP = 10
MIN_FREE_GIB = 20
# Abort if the USB drive has less than this much free space.
SKIP_IMMICH = False

# =============================================================================
# Toolkit
# =============================================================================

SCRIPT_VERSION = "1.3.0"