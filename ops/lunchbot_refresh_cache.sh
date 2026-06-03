#!/usr/bin/env bash
set -euo pipefail

REPO="${DELIVERY_API_REPO:-/path/to/delivery_api}"
OUT_DIR="${LUNCHBOT_DATA_DIR:-/path/to/lunchbot-data}"
LOG="$OUT_DIR/refresh.log"
FEED="$OUT_DIR/raw_feed.md"

mkdir -p "$OUT_DIR"
cd "$REPO"

scrape_status=0
{
  date -Is
  pixi run scrape || scrape_status=$?
  if [ "$scrape_status" -ne 0 ]; then
    echo "scrape exited with status $scrape_status"
  fi
  pixi run lunch-feed-cache-compact > "$FEED"
} >> "$LOG" 2>&1
