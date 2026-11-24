#!/usr/bin/env bash
# Usage: scripts/backfill_camera_odg.sh FIRST_MONTH LAST_MONTH   (e.g. 2022-10 2026-11)
# Runs one GitHub Actions dispatch per month, sequentially; stops at the first failure and prints the resume command.
set -euo pipefail
first_month="${1:?first month YYYY-MM}"; last_month="${2:?last month YYYY-MM}"
month="$first_month"
next_month() { python3 -c "import sys;y,m=map(int,sys.argv[1].split('-'));y,m=(y+1,1) if m==12 else (y,m+1);print(f'{y:04d}-{m:02d}')" "$1"; }
last_day() { python3 -c "import sys,calendar;y,m=map(int,sys.argv[1].split('-'));print(f'{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}')" "$1"; }
while [[ ! "$month" > "$last_month" ]]; do
  start="$month-01"; end="$(last_day "$month")"
  echo "=== camera_odg $start .. $end"
  prev_id="$(gh run list --workflow ingest-amendments.yml --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId // 0')"
  gh workflow run ingest-amendments.yml -f source=camera_odg -f start_date="$start" -f end_date="$end" -f max_items=5000
  run_id="$prev_id"
  for _ in $(seq 1 60); do
    sleep 5
    run_id="$(gh run list --workflow ingest-amendments.yml --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId // 0')"
    [[ "$run_id" != "$prev_id" ]] && break
  done
  if [[ "$run_id" == "$prev_id" ]]; then
    echo "dispatch for $month did not appear within 5 minutes. Resume with: $0 $month $last_month" >&2
    exit 1
  fi
  if ! gh run watch "$run_id" --exit-status; then
    echo "FAILED month $month (run $run_id). Resume with: $0 $month $last_month" >&2
    exit 1
  fi
  month="$(next_month "$month")"
done
echo "backfill completed: $first_month .. $last_month"
