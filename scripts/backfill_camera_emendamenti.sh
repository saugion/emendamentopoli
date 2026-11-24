#!/usr/bin/env bash
# Usage: scripts/backfill_camera_emendamenti.sh [N]   (default N=20)
# Runs N GitHub Actions dispatches of camera_emendamenti (max_atti=0, max_liste=5000,
# max_detail=5000), sequentially; stops early when a run's result shows details_fetched == 0
# (nothing new left). Stops at the first failure and prints the resume command.
set -euo pipefail
n="${1:-20}"
for i in $(seq 1 "$n"); do
  echo "=== camera_emendamenti run $i/$n"
  prev_id="$(gh run list --workflow ingest-amendments.yml --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId // 0')"
  gh workflow run ingest-amendments.yml -f source=camera_emendamenti -f max_atti=0 -f max_liste=5000 -f max_detail=5000
  run_id="$prev_id"
  for _ in $(seq 1 60); do
    sleep 5
    run_id="$(gh run list --workflow ingest-amendments.yml --event workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId // 0')"
    [[ "$run_id" != "$prev_id" ]] && break
  done
  if [[ "$run_id" == "$prev_id" ]]; then
    echo "dispatch $i did not appear within 5 minutes. Resume with: $0 $((n - i + 1))" >&2
    exit 1
  fi
  if ! gh run watch "$run_id" --exit-status; then
    echo "FAILED run $i (run $run_id). Resume with: $0 $((n - i + 1))" >&2
    exit 1
  fi
  fetched="$(gh run view "$run_id" --log 2>/dev/null | grep -o '"details_fetched": [0-9]*' | tail -1 | grep -o '[0-9]*$')"
  echo "run $i details_fetched=${fetched:-?}"
  if [[ "${fetched:-1}" == "0" ]]; then
    echo "nothing new left (details_fetched=0): backfill completed after $i runs"
    exit 0
  fi
done
echo "backfill loop completed after $n runs"
