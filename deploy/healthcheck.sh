#!/usr/bin/env bash
# Runs every 15 min. Alerts to Telegram if something is wrong.
# Silence is not health — this checks liveness, integrity and drift.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

alert() {
  echo "ALERT: $1" >&2
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" -d parse_mode=HTML \
    --data-urlencode "text=<b>⚠️ Healthcheck</b>
$1" >/dev/null
}

# 1. container running
if ! docker compose ps --format '{{.State}}' 2>/dev/null | grep -q running; then
  alert "Container is NOT running. Open positions are unmanaged."; exit 1
fi

# 2. the loop is actually looping (state file is touched every cycle)
if [ -f data/state.json ]; then
  age=$(( $(date +%s) - $(stat -c %Y data/state.json) ))
  [ "$age" -gt 900 ] && alert "State file is ${age}s stale — the loop may be wedged."
fi

# 3. audit chain intact
if ! docker compose exec -T trader python audit.py verify 2>/dev/null | grep -q "CHAIN INTACT"; then
  alert "AUDIT LEDGER FAILED VERIFICATION. Stop trading and investigate."
fi

# 4. any failure events since yesterday
fails=$(docker compose exec -T trader python - <<'PY' 2>/dev/null
from ct.ledger import Ledger
import os, datetime
L = Ledger(os.environ.get("LEDGER_FILE", "/data/ledger.db"))
cut = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()
print(len(L.q("SELECT 1 FROM ledger WHERE ok=0 AND ts > ?", (cut,))))
PY
)
[ "${fails:-0}" -gt 0 ] && alert "${fails} failure event(s) in the last 24h. Run: audit.py failures"

# 5. disk
use=$(df -P . | awk 'NR==2{gsub("%","",$5);print $5}')
[ "$use" -gt 85 ] && alert "Disk ${use}% full."
exit 0
