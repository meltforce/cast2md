#!/bin/bash
# Backup SQLite database to NAS
# Run via cron: 0 */6 * * * /opt/cast2md/deploy/backup.sh
#
# This script uses cast2md's built-in backup command to create
# consistent database snapshots (handles WAL mode properly).

set -e

BACKUP_DIR="/mnt/nas/cast2md/backups"
APP_DIR="/opt/cast2md"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Ensure backup directory exists
mkdir -p $BACKUP_DIR

# Use cast2md's backup command for consistent copy
cd $APP_DIR
.venv/bin/python -m cast2md backup -o "$BACKUP_DIR/cast2md_backup_$TIMESTAMP.db"

# Keep only last 7 days of backups
find $BACKUP_DIR -name "cast2md_backup_*.db" -mtime +7 -delete

echo "Backup complete: cast2md_backup_$TIMESTAMP.db"
