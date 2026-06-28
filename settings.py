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

SCRIPT_VERSION = "1.1.0"