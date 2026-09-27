# Changelog

## 1.0.0
- Initial version

## 1.1.0
- Added Git commit
- Added backup.info
- Added folder sizes
- Human readable duration

## 1.2.0
- Immich now backed up with Borg instead of rsync: encrypted, deduplicated,
  versioned snapshots (BORG_KEEP_DAILY/WEEKLY/MONTHLY) instead of one
  mirrored copy with no history
- Requires `borg` installed (apt install borgbackup) and BORG_PASSPHRASE
  set in settings.py
- vzdump and the /etc/pve, /root, network, fstab config mirror are
  unchanged — still plain rsync

  ## 1.3.0
- Added a second Borg repo for /mnt/sata_thin_pool/keepass_dbs, separate
  from Immich's repo (own retention: KEEPASS_KEEP_DAILY/WEEKLY/MONTHLY,
  set long by default since the data is tiny — SKIP_KEEPASS to disable)
- Refactored immich-only borg_backup_immich() into a generic borg_backup()
  so adding further Borg sources later is a ~10-line addition, not a
  copy-pasted function
