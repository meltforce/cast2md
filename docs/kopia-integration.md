# Kopia Backup Integration

This document describes how cast2md integrates with [Kopia](https://kopia.io/) for incremental, deduplicated backups.

## Overview

The backup script (`deploy/kopia-backup.sh`) runs daily at 5:00 AM and creates two snapshots:

1. **Database snapshot**: PostgreSQL dump to `/tmp/cast2md-backup.sql` (deleted after backup)
2. **Transcripts snapshot**: `/mnt/nas/cast2md` (excludes audio, trash, temp files)

### What's Backed Up

| Source | Snapshot | Size | Notes |
|--------|----------|------|-------|
| PostgreSQL database | `/tmp/cast2md-backup.sql` | ~3 GB | Full dump, deleted after snapshot |
| Transcripts | `/mnt/nas/cast2md/transcripts/` | ~50 MB | Markdown files |

### What's Excluded (via policy)

| Pattern | Reason |
|---------|--------|
| `audio/` | Large files, re-downloadable from RSS |
| `trash/` | Auto-cleaned after 30 days |
| `*.tmp`, `*.part` | Temporary files |
| `backups/cast2md_backup_*.sql` | Old timestamped dumps |

## Production Setup (cast2md server)

### Current Configuration

- **Cron**: Daily at 5:00 AM
- **Log file**: `/var/log/cast2md-backup.log`
- **Repository**: CIFS mount at `/mnt/kopia-repo`

### Verify Setup

```bash
# Check cron
crontab -l

# Check repository connection
kopia repository status

# Check ignore policy
kopia policy show /mnt/nas/cast2md

# View recent snapshots
kopia snapshot list
```

## Manual Operations

### Run Backup Now

```bash
/opt/cast2md/deploy/kopia-backup.sh
```

### List Snapshots

```bash
# All snapshots for this host
kopia snapshot list

# Example output:
# root@cast2md:/mnt/nas/cast2md
#   2026-01-22 17:51:27 CET kd635... 51.1 MB (transcripts)
#
# root@cast2md:/tmp/cast2md-backup.sql
#   2026-01-22 17:51:27 CET Ix66c... 3.1 GB (database)
```

## Restoration

### Restore Database

```bash
# 1. List database snapshots
kopia snapshot list | grep cast2md-backup.sql

# 2. Restore to temp file (use snapshot ID from list)
kopia snapshot restore <snapshot-id> /tmp/restored-db.sql

# 3. Verify the dump
head -50 /tmp/restored-db.sql
tail -20 /tmp/restored-db.sql

# 4. Restore to PostgreSQL
docker exec -i cast2md-postgres-1 psql -U cast2md -d cast2md < /tmp/restored-db.sql

# Or using cast2md CLI:
cd /opt/cast2md && source .venv/bin/activate
cast2md restore /tmp/restored-db.sql

# 5. Cleanup
rm /tmp/restored-db.sql
```

### Restore Transcripts

```bash
# 1. List transcript snapshots
kopia snapshot list | grep /mnt/nas/cast2md

# 2. Restore entire directory
kopia snapshot restore <snapshot-id> /tmp/restored-transcripts/

# 3. Or restore specific feed
kopia snapshot restore <snapshot-id>/transcripts/Huberman_Lab /tmp/huberman/

# 4. Copy back to production
cp -r /tmp/restored-transcripts/transcripts/* /mnt/nas/cast2md/transcripts/
```

### Restore to Specific Point in Time

```bash
# List all snapshots with dates
kopia snapshot list --all

# Restore from specific snapshot
kopia snapshot restore <older-snapshot-id> /tmp/restore/
```

## Verification

The restore process was tested and verified:

| Test | Result |
|------|--------|
| Database dump restore | Valid PostgreSQL dump with pgvector extension |
| Transcript restore | 460 files across 11 feeds restored correctly |

### Test Restore Yourself

```bash
# Restore database to temp location
kopia snapshot restore $(kopia snapshot list | grep backup.sql | head -1 | awk '{print $4}') /tmp/test-db.sql

# Verify it's valid SQL
head -50 /tmp/test-db.sql
tail -20 /tmp/test-db.sql

# Should show:
# - "PostgreSQL database dump" header
# - "PostgreSQL database dump complete" footer

# Cleanup
rm /tmp/test-db.sql
```

## Troubleshooting

### "Repository not connected"

```bash
# Check connection
kopia repository status

# Reconnect if needed (password in /root/.kopia-password-env)
source /root/.kopia-password-env
kopia repository connect filesystem --path /mnt/kopia-repo
```

### "Permission denied" on repository

The repository is a CIFS mount. For unprivileged LXC containers, ensure the mount uses the correct uid mapping (container root maps to host uid ~100000).

### Backup fails but kopia works

Check if PostgreSQL is running:
```bash
docker ps | grep postgres

# Start if needed
cd /opt/cast2md && docker compose up -d postgres
```

### View Backup Logs

```bash
# Recent log entries
tail -50 /var/log/cast2md-backup.log

# Follow live
tail -f /var/log/cast2md-backup.log
```

## File Reference

| File | Purpose |
|------|---------|
| `deploy/kopia-backup.sh` | Main backup script (runs via cron) |
| `deploy/backup.sh` | Standalone DB backup (manual use only) |
| `/var/log/cast2md-backup.log` | Backup log file |
| `/mnt/kopia-repo` | Kopia repository mount point |
