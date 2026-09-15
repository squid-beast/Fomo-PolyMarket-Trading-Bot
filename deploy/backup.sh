#!/usr/bin/env bash
# Back up the audit ledger and verify the copy is readable and intact.
# A backup you have never restored is not a backup.
set -euo pipefail
cd "$(dirname "$0")/.."

TS=$(date -u +%Y%m%dT%H%M%SZ)
DEST="${BACKUP_DIR:-$HOME/backups}"
mkdir -p "$DEST"; chmod 700 "$DEST"

for db in data/ledger.db data/paper.db; do
  [ -f "$db" ] || continue
  name=$(basename "$db" .db)
  out="$DEST/${name}-${TS}.db"
  # sqlite .backup is safe against a live writer; cp is not
  sqlite3 "$db" ".backup '$out'" 2>/dev/null || cp -a "$db" "$out"
  gzip -f "$out"
done

# prove the ledger backup actually verifies
LATEST=$(ls -t "$DEST"/ledger-*.db.gz 2>/dev/null | head -1 || true)
if [ -n "$LATEST" ]; then
  tmp=$(mktemp -d); gunzip -c "$LATEST" > "$tmp/l.db"
  if LEDGER_FILE="$tmp/l.db" python3 audit.py verify | grep -q "CHAIN INTACT"; then
    echo "backup ok + verified: $LATEST"
  else
    echo "WARNING: backup $LATEST does not verify" >&2
    exit 1
  fi
  rm -rf "$tmp"
fi

# retention: 30 days
find "$DEST" -name '*.db.gz' -mtime +30 -delete
