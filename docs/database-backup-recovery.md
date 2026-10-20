# SQLite backup and recovery procedure

## Important limitation

These tools create and validate a consistent SQLite copy. **They do not make an ephemeral hosting filesystem durable, schedule backups, or provide off-site retention by themselves.** The current Render service is not confirmed to have a persistent disk attached. Do not store real student or finance data until persistent storage (or a supported managed database) and off-host backups have been configured and verified.

## Commands

From the repository root:

```bash
# Make a timestamped, integrity-checked copy.
DATABASE_PATH=/var/data/glihs.db BACKUP_DIR=/var/data/backups \
  python scripts/sqlite_backup.py backup

# Check a backup before attempting recovery.
python scripts/sqlite_backup.py verify /var/data/backups/glihs-YYYYMMDDTHHMMSSZ.sqlite3

# Restore into a NEW destination first; this avoids overwriting the current database.
python scripts/sqlite_backup.py restore \
  /var/data/backups/glihs-YYYYMMDDTHHMMSSZ.sqlite3 \
  /var/data/recovery-check.sqlite3

# Only after validating the restored copy and an approved maintenance window,
# replace a target database deliberately with --overwrite.
```

Set `DATABASE_PATH` to the actual database path used by the deployed app. Set `BACKUP_DIR` to a directory on durable storage, not a temporary container filesystem. Use a separate protected off-site destination for copies; do not put backups in a public/static directory or commit them to Git.

## Minimum operational checklist

1. Attach/configure durable storage or migrate to a managed database; confirm the app's database path points to it.
2. Run a backup after a controlled fictional-data change and verify the output reports integrity OK.
3. Copy that backup to a separately protected location with restricted access and retention.
4. Restore to a separate disposable path, check the expected fictional records and key tables, and document the time taken.
5. Test recovery after deployment/restart before onboarding any real school.
6. Schedule backups and alert on failures using the hosting provider or a protected scheduler. These scripts are manual CLI tools and do not schedule themselves.
7. Restrict access to backup files; they contain sensitive records. The script attempts to set file mode 0600 on supported filesystems.

## Release gate

A backup command passing is not proof of production recovery. Production readiness requires durable storage, automated off-host backup, a successful restore drill, a documented retention policy, and a tested rollback/migration procedure.
