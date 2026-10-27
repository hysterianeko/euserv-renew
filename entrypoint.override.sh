#!/bin/sh
set -eu

RUN_MODE="${RUN_MODE:-loop}"
RUN_INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-86400}"
RUN_ON_STARTUP="${RUN_ON_STARTUP:-true}"

run_once() {
  echo "[entrypoint] $(date -u '+%Y-%m-%dT%H:%M:%SZ') running renewal job"
  python /app/main_override.py
}

case "$RUN_MODE" in
  once)
    exec python /app/main_override.py
    ;;
  loop)
    if [ "$RUN_ON_STARTUP" = "true" ]; then
      run_once || true
    fi
    while true; do
      echo "[entrypoint] sleeping ${RUN_INTERVAL_SECONDS}s before next run"
      sleep "$RUN_INTERVAL_SECONDS"
      run_once || true
    done
    ;;
  *)
    echo "[entrypoint] unsupported RUN_MODE: $RUN_MODE" >&2
    exit 1
    ;;
esac
