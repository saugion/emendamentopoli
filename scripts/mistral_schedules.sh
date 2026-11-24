#!/usr/bin/env bash
# Creates the Mistral-side nightly schedules of the Camera workflows (idempotent: skips existing ones).
# Requires MISTRAL_API_KEY (key with access to the workspace workflows). Does not touch the Senate schedule.
set -euo pipefail
: "${MISTRAL_API_KEY:?MISTRAL_API_KEY is required}"
API_BASE="https://api.mistral.ai/v1"
DEPLOYMENT_NAME="emendamentopoli"

existing="$(curl -sS -H "Authorization: Bearer $MISTRAL_API_KEY" "$API_BASE/workflows/schedules")"
echo "$existing" | jq '.'

create() {
  local workflow="$1" cron="$2" input="$3"
  if echo "$existing" | jq -e --arg w "$workflow" '[.schedules[]? | select(.workflow_name == $w)] | length > 0' >/dev/null; then
    echo "schedule for $workflow already exists: skipped"
    return
  fi
  payload="$(jq -n --arg w "$workflow" --arg d "$DEPLOYMENT_NAME" --arg c "$cron" --argjson i "$input" \
    '{workflow_identifier: $w, deployment_name: $d, schedule: {input: $i, cron_expressions: [$c]}}')"
  curl -sS -X POST "$API_BASE/workflows/schedules" \
    -H "Authorization: Bearer $MISTRAL_API_KEY" -H "Content-Type: application/json" -d "$payload" | jq '.'
}

create "camera-atti" "5 4 * * *" '{"legislature": 19, "full": false}'
create "camera-odg-giornalieri" "40 4 * * *" '{"legislature": 19, "max_items": 1500, "evaluate": true}'
create "camera-emendamenti-giornalieri" "45 6 * * *" '{"legislature": 19, "max_atti": 300, "max_liste": 600, "max_detail": 2500, "evaluate": true}'
