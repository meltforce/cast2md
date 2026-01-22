#!/bin/bash
# End-to-end backup: fresh DB dump + Kopia snapshot
# Run via cron: 0 */6 * * * /opt/cast2md/deploy/kopia-backup.sh
set -euo pipefail

HOSTNAME=$(hostname)
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
LOG_PREFIX="[Kopia Backup - $HOSTNAME]"
LOG_FILE="/var/log/cast2md-backup.log"

CAST2MD_DIR="/opt/cast2md"
DB_DUMP="/tmp/cast2md-backup.sql"

log() {
    echo "$LOG_PREFIX $TIMESTAMP - $1" | tee -a "$LOG_FILE"
}

cleanup() {
    if [[ -f "$DB_DUMP" ]]; then
        rm -f "$DB_DUMP"
        log "Cleaned up temporary dump file"
    fi
}
trap cleanup EXIT

log "Starting cast2md backup"

# Ensure repository is connected
if ! kopia repository status &>/dev/null; then
    log "ERROR: Kopia repository not connected"
    exit 1
fi

BACKUP_FAILED=0

# 1. Create fresh database dump (local disk for speed)
log "Creating database dump"
cd "$CAST2MD_DIR"
source .venv/bin/activate

if cast2md backup -o "$DB_DUMP" 2>&1 | tee -a "$LOG_FILE"; then
    log "SUCCESS: Database dump completed"
else
    log "ERROR: Database dump failed"
    BACKUP_FAILED=1
fi

# 2. Kopia snapshot - transcripts from NAS
log "Creating Kopia snapshot of /mnt/nas/cast2md (transcripts)"

if kopia snapshot create /mnt/nas/cast2md \
    --description="cast2md transcripts from $HOSTNAME" \
    --tags="host:$HOSTNAME,app:cast2md,type:transcripts,timestamp:$(date +%s)" \
    2>&1 | tee -a "$LOG_FILE"; then
    log "SUCCESS: Transcripts snapshot completed"
else
    log "ERROR: Transcripts snapshot failed"
    BACKUP_FAILED=1
fi

# 3. Kopia snapshot - database dump
if [[ -f "$DB_DUMP" ]]; then
    log "Creating Kopia snapshot of database dump"

    if kopia snapshot create "$DB_DUMP" \
        --description="cast2md database from $HOSTNAME" \
        --tags="host:$HOSTNAME,app:cast2md,type:database,timestamp:$(date +%s)" \
        2>&1 | tee -a "$LOG_FILE"; then
        log "SUCCESS: Database snapshot completed"
    else
        log "ERROR: Database snapshot failed"
        BACKUP_FAILED=1
    fi
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
