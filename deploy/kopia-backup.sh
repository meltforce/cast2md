#!/bin/bash
# End-to-end backup: fresh DB dump + Kopia snapshot
# Run via cron: 0 */6 * * * /opt/cast2md/deploy/kopia-backup.sh
set -euo pipefail

HOSTNAME=$(hostname)
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
LOG_PREFIX="[Kopia Backup - $HOSTNAME]"
LOG_FILE="/var/log/cast2md-backup.log"

BACKUP_DIR="/mnt/nas/cast2md/backups"
CAST2MD_DIR="/opt/cast2md"

log() {
    echo "$LOG_PREFIX $TIMESTAMP - $1" | tee -a "$LOG_FILE"
}

log "Starting cast2md backup"

# Ensure repository is connected
if ! kopia repository status &>/dev/null; then
    log "ERROR: Kopia repository not connected"
    exit 1
fi

BACKUP_FAILED=0

# 1. Create fresh database dump
log "Creating database dump"
cd "$CAST2MD_DIR"
source .venv/bin/activate

if cast2md backup -o "$BACKUP_DIR/latest.sql" 2>&1 | tee -a "$LOG_FILE"; then
    log "SUCCESS: Database dump completed"
else
    log "ERROR: Database dump failed"
    BACKUP_FAILED=1
fi

# 2. Kopia snapshot (DB + transcripts only)
log "Creating Kopia snapshot of /mnt/nas/cast2md"

if kopia snapshot create /mnt/nas/cast2md \
    --description="cast2md backup from $HOSTNAME" \
    --tags="host:$HOSTNAME,app:cast2md,timestamp:$(date +%s)" \
    --ignore-rules-file "$CAST2MD_DIR/deploy/.kopiaignore" \
    2>&1 | tee -a "$LOG_FILE"; then
    log "SUCCESS: Kopia snapshot completed"
else
    log "ERROR: Kopia snapshot failed"
    BACKUP_FAILED=1
fi

# Weekly maintenance on Sundays
if [[ $(date +%u) -eq 7 ]]; then
    log "Running weekly repository maintenance"
    kopia maintenance run --full 2>&1 | tee -a "$LOG_FILE"
fi

if [[ $BACKUP_FAILED -eq 0 ]]; then
    log "Backup completed successfully"
    exit 0
else
    log "Backup completed with errors"
    exit 1
fi
