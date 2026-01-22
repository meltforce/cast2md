#!/bin/bash
# Standalone PostgreSQL database backup (no retention management)
#
# This script creates a timestamped database dump for manual/standalone use.
# For production backups with Kopia integration, use kopia-backup.sh instead.
#
# This script uses cast2md's built-in backup command to create
# consistent database snapshots using pg_dump.

set -e

BACKUP_DIR="/mnt/nas/cast2md/backups"
APP_DIR="/opt/cast2md"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Ensure backup directory exists
mkdir -p $BACKUP_DIR

# Use cast2md's backup command for consistent copy
cd $APP_DIR
.venv/bin/python -m cast2md backup -o "$BACKUP_DIR/cast2md_backup_$TIMESTAMP.sql"

echo "Backup complete: cast2md_backup_$TIMESTAMP.sql"
