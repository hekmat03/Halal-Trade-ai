#!/usr/bin/env bash
# Best-effort crash supervisor for the HalalTrade AI 30-day PAPER run.
#
# WHAT THIS IS — AND IS NOT
# -------------------------
# This only protects against a PLAIN PROCESS CRASH while the box is alive (OOM
# kill, unhandled error that escapes the loop, an accidental `kill`). It does
# NOT and cannot survive the platform replacing the machine: nothing this script
# does runs on the new box, and systemd state does not travel with it.
#
# The actual machine-swap guarantee lives in the runner itself:
#   backend/run_paper_trading.py persists the report-scheduler state to
#   backend/paper_run_state.json and, on the next start, delivers at most one
#   missed daily + one missed weekly Telegram report (bounded catch-up, no
#   double-sends, no backfill spam). This script is defense in depth only.
#
# SAFETY PROPERTIES
# * Paper only. The flags below are byte-identical to the owner's launch (the
#   same flags /home/team/shared/relaunch_paper_run.sh uses); no size, strategy,
#   config or gate value is changed here, and live trading stays OFF (the runner
#   forces trading_mode=paper / live_enabled=False).
# * Never starts a second instance: if any `python ... run_paper_trading.py`
#   process is alive it does nothing at all.
# * Writes nothing except its own log file (`supervise_paper_run.log`) and the
#   runner's usual log; it does not touch halaltrade.db, .env, or the API server.
# * No backend/.env => no config => NO-OP. It never creates, edits or invents
#   configuration, and it never blocks waiting for input.
# * Backoff: repeated instant deaths (a bad config, a broken venv) are retried
#   with exponential backoff (up to 15 min) instead of a hot restart loop.
#
# USAGE
#   backend/supervise_paper_run.sh              # loop forever, 60 s interval
#   backend/supervise_paper_run.sh --once       # one check, then exit
#   backend/supervise_paper_run.sh --status     # report liveness, then exit
# Env overrides (all optional):
#   HALAL_SUPERVISOR_INTERVAL   seconds between checks (default 60)
#   HALAL_SUPERVISOR_DRY_RUN=1  log what it would do, never launch anything
#   HALAL_SUPERVISOR_LOG        supervisor log path
#   HALAL_SUPERVISOR_BACKEND_DIR / HALAL_SUPERVISOR_PYTHON  path overrides
#
# NOT installed, NOT enabled, NOT attached to the currently running process by
# this repository. Installing it is the owner's explicit decision (see
# backend/deploy/README.md).
set -u

BACKEND_DIR="${HALAL_SUPERVISOR_BACKEND_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PYTHON="${HALAL_SUPERVISOR_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
RUNNER="$BACKEND_DIR/run_paper_trading.py"
ENV_FILE="$BACKEND_DIR/.env"
RUN_LOG="${HALAL_SUPERVISOR_RUN_LOG:-$BACKEND_DIR/paper_run.log}"
SUP_LOG="${HALAL_SUPERVISOR_LOG:-$BACKEND_DIR/supervise_paper_run.log}"
INTERVAL="${HALAL_SUPERVISOR_INTERVAL:-60}"
DRY_RUN="${HALAL_SUPERVISOR_DRY_RUN:-0}"

# Only matches a python process running the runner (never this script, never an
# editor or a `tail` on the log). Overridable only so a dry run can be exercised
# without a real run present — leave it alone in production.
PROC_PATTERN="${HALAL_SUPERVISOR_PROC_PATTERN:-python[0-9.]* .*run_paper_trading\.py}"

# The owner's canonical launch flags — identical to the live run
# (PID 299) and to /home/team/shared/relaunch_paper_run.sh. `--symbol` is left
# at its default (BTCUSDT) so the relaunched cmdline matches exactly.
RUN_FLAGS=(
  --strategy donchian_1d
  --timeframe 1d
  --starting-usdt 1000
  --trade-usdt 100
  --poll-seconds 3600
  --daily-report-utc 18:00
  --weekly-report-day SUN
  --weekly-report-utc 12:00
)

log() {
  local line
  line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') [supervise] $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$SUP_LOG" 2>/dev/null || true
}

# PIDs of a live runner process (empty when none).
running_pids() {
  pgrep -f "$PROC_PATTERN" 2>/dev/null | tr '\n' ' '
}

is_alive() {
  [ -n "$(running_pids | tr -d ' ')" ]
}

launch() {
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY RUN: would launch '$PYTHON $RUNNER ${RUN_FLAGS[*]}'"
    return 0
  fi
  log "launching: $PYTHON $RUNNER ${RUN_FLAGS[*]}"
  # setsid + nohup: survives this shell; output goes to the run's own log, so
  # the audit trail / Telegram behaviour is unchanged.
  ( cd "$BACKEND_DIR" && setsid nohup "$PYTHON" "$RUNNER" "${RUN_FLAGS[@]}" >> "$RUN_LOG" 2>&1 & )
  sleep 8
  if is_alive; then
    log "runner is up (pid $(running_pids))"
    return 0
  fi
  log "launch did not come up — see $RUN_LOG"
  return 1
}

check() {
  # 0 = nothing to do (alive or deliberately idle), 1 = a relaunch was attempted
  if [ ! -f "$ENV_FILE" ]; then
    log "no $ENV_FILE — nothing to supervise (no-op)"
    return 0
  fi
  if is_alive; then
    return 0
  fi
  log "paper run is NOT running — relaunching"
  launch
}

main() {
  local mode="loop"
  case "${1:-}" in
    --once) mode="once" ;;
    --status) mode="status" ;;
    --help|-h) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
    "") ;;
    *) printf 'unknown argument %s (try --help)\n' "$1" >&2; return 2 ;;
  esac

  log "start | backend='$BACKEND_DIR' interval=${INTERVAL}s dry_run=$DRY_RUN mode=$mode"
  if [ ! -f "$RUNNER" ]; then
    log "run_paper_trading.py not found at $RUNNER — refusing to do anything"
    return 2
  fi
  if [ ! -x "$PYTHON" ]; then
    log "interpreter not found at $PYTHON — refusing to do anything"
    return 2
  fi

  if [ "$mode" = "status" ]; then
    if is_alive; then
      log "alive: $(running_pids)"
      return 0
    fi
    log "not running"
    return 1
  fi

  if [ ! -f "$ENV_FILE" ]; then
    log "no $ENV_FILE — nothing to supervise; exiting"
    return 0
  fi
  if is_alive; then
    log "a paper run is already alive (pid $(running_pids)) — monitoring only, never a second instance"
  fi

  if [ "$mode" = "once" ]; then
    check || true
    log "single check done — exiting"
    return 0
  fi

  local backoff=0
  while :; do
    if ! is_alive; then
      check || true
      if is_alive; then
        backoff=0
      else
        backoff=$(( backoff == 0 ? 60 : backoff * 2 ))
        [ "$backoff" -gt 900 ] && backoff=900
        log "relaunch not effective — backing off ${backoff}s"
        sleep "$backoff"
        continue
      fi
    else
      backoff=0
    fi
    sleep "$INTERVAL"
  done
}

main "$@"
