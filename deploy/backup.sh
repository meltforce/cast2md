#!/bin/bash
# Backup PostgreSQL database to NAS
# Run via cron: 0 */6 * * * /opt/cast2md/deploy/backup.sh
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

# Keep only last 7 days of backups
find $BACKUP_DIR -name "cast2md_backup_*.sql" -mtime +7 -delete

echo "Backup complete: cast2md_backup_$TIMESTAMP.sql"
