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
  versioned snapshots instead of one mirrored copy with no history
- Requires `borg` installed (apt install borgbackup) and BORG_PASSPHRASE
  set in settings.py
- vzdump and the /etc/pve, /root, network, fstab config mirror are
  unchanged — still plain rsync

## 1.3.0
- Added a second Borg repo for /mnt/sata_thin_pool/keepass_database, separate
  from Immich's repo (own retention: KEEPASS_KEEP_DAILY/WEEKLY/MONTHLY,
  set long by default since the data is tiny — SKIP_KEEPASS to disable)
- Refactored immich-only borg_backup_immich() into a generic borg_backup()
  so adding further Borg sources later is a ~10-line addition, not a
  copy-pasted function
- Renamed BORG_KEEP_DAILY/WEEKLY/MONTHLY to IMMICH_KEEP_DAILY/WEEKLY/MONTHLY
  — the old names looked like global Borg settings but were Immich-only;
  BORG_PASSPHRASE stays as-is since it's genuinely shared across repos

## 1.4.0
- Simplified for manual use: removed Proxmox version, kernel, git commit,
  host/hostname and filesystem fields from backup.info; removed automatic
  unmounting of a "wrong" drive (the script now stops instead) and some dead
  code
- Startup validation: empty USB_UUID, empty BORG_PASSPHRASE or missing `borg`
  now fail immediately, before anything is mounted or unmounted
- Safety: refuses to rsync (--delete) or Borg-backup a source that is empty
  (e.g. /etc/pve or the Immich disk not mounted), so a good backup can no
  longer be wiped or pruned away by an empty source
- Borg exit code 1 (warnings) and rsync exit code 24 (vanished files) no
  longer abort the run
- backup.info Status is now Running -> Success / Failed; Last Backup only
  changes on success, so an aborted run never looks like a good one
- Fixed: KeePass size was never written to backup.info
- KeePass is now backed up first
- /root is synced without .cache/ (Borg's local cache)
- New `check` command (`check --deep` for full data verification) and a
  "Last Check" line in backup.info
- rsync progress output no longer floods the log files
- Immich restore docs corrected (Borg strips the leading "/")
- settings.py regrouped: drive, sources, encryption, retention, options;
  SCRIPT_VERSION moved into backup.py
- README updated: Borg repos in layout, Python 3.9+, borg >= 1.2