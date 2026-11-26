#!/usr/bin/env bash
# Best-effort crash supervisor for HalalTrade AI PAPER runs (one supervisor per run).
#
# WHAT THIS IS — AND IS NOT
# -------------------------
# This only protects against a PLAIN PROCESS CRASH while the box is alive (OOM
# kill, unhandled error that escapes the loop, an accidental `kill`). It does
# NOT and cannot survive the platform replacing the machine: nothing this script
# does runs on the new box, and no state travels with it. After a machine swap
# you still run /home/team/shared/start_paper_runs.sh once.
#
# The machine-swap recovery that does exist lives in the runner itself:
#   backend/run_paper_trading.py persists the report-scheduler state to its
#   --state-file and, on the next start, delivers at most one missed daily +
#   one missed weekly Telegram report (bounded catch-up, no double-sends).
#   Each run's 18-month clock is anchored in its own DB, so a restart resumes
#   the same clock. This script is defense in depth only.
#
# PER-RUN MODE (what the two live paper runs use)
# ----------------------------------------------
#   supervise_paper_run.sh --run-label 1D-CH15 \
#       --run-log /home/team/shared/paper_run_1d_ch15.log \
#       [--interval 15] [--supervisor-log /path.log] \
#       -- --strategy donchian_1d --timeframe 1d ... --run-label 1D-CH15 \
#          --db-path ... --state-file ...
#
# Everything after `--` is the run's OWN launch argv, passed through verbatim, so
# the supervisor restarts the run with exactly the flags it was started with
# (same strategy, timeframe, size, window, report slots, DB, state file). The
# runner path is added by this script and is deliberately NOT part of the
# supervisor's own command line, so `pgrep -f run_paper_trading.py` never
# mistakes a supervisor for a run.
#
# A per-run supervisor only ever looks at ITS OWN label:
#   * it is alive-and-idle whenever that label has a live runner — it never
#     restarts, touches or masks another label's run;
#   * if that label has no live runner it relaunches it, and then verifies;
#   * one supervisor per label: it holds an exclusive flock on its supervisor
#     log, so a second supervisor for the same label exits immediately;
#   * never a duplicate run: it re-checks the label before launching, and after
#     a launch any *additional* process for the same label is stopped (oldest
#     pid wins). It never signals a process of another label.
#
# SAFETY PROPERTIES
# * Paper only. Every launch exports HALAL_TRADING_MODE=paper and
#   HALAL_LIVE_ENABLED=false; no size, strategy, config or gate value is changed
#   and live trading stays OFF (no live-order path exists in the runner).
# * It refuses to supervise unless its argv carries --run-label <label>, so it
#   can never start a run that reports under the wrong label.
# * It writes nothing except its own supervisor log (+ the per-run flock file)
#   and the run's usual log; it does not touch halaltrade.db, .env or the API.
# * No backend/.env => no config => NO-OP (legacy mode); it never creates or
#   edits configuration, and it never blocks waiting for input.
# * Backoff: repeated instant deaths (bad config, broken venv) are retried with
#   exponential backoff (60 s doubling, capped at 15 min) instead of a hot loop.
#
# LEGACY SINGLE-RUN MODE (unchanged; do not use it with two runs — it no-ops
# whenever ANY run_paper_trading.py is alive, so it cannot tell the runs apart):
#   backend/supervise_paper_run.sh              # loop forever, 60 s interval
#   backend/supervise_paper_run.sh --once       # one check, then exit
#   backend/supervise_paper_run.sh --status     # report liveness, then exit
#
# Env overrides (all optional):
#   HALAL_SUPERVISOR_INTERVAL   seconds between checks (default 60 legacy, 15 per-run)
#   HALAL_SUPERVISOR_DRY_RUN=1  log what it would do, never launch anything
#   HALAL_SUPERVISOR_LOG        supervisor log path
#   HALAL_SUPERVISOR_BACKEND_DIR / HALAL_SUPERVISOR_PYTHON  path overrides
#
# Installing the systemd unit is the owner's explicit decision (see
# backend/deploy/README.md). /home/team/shared/start_paper_runs.sh starts one
# per-run supervisor per paper run.
set -u

BACKEND_DIR="${HALAL_SUPERVISOR_BACKEND_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PYTHON="${HALAL_SUPERVISOR_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
RUNNER="$BACKEND_DIR/run_paper_trading.py"
ENV_FILE="$BACKEND_DIR/.env"

# Defaults (legacy single-run mode).
LABEL=""
RUN_ARGS=()
RUN_LOG="${HALAL_SUPERVISOR_RUN_LOG:-$BACKEND_DIR/paper_run.log}"
SUP_LOG="${HALAL_SUPERVISOR_LOG:-$BACKEND_DIR/supervise_paper_run.log}"
INTERVAL="${HALAL_SUPERVISOR_INTERVAL:-}"
DRY_RUN="${HALAL_SUPERVISOR_DRY_RUN:-0}"

# Only matches a python process running the runner (never this script, never an
# editor or a `tail` on the log). Overridable only so a dry run can be exercised
# without a real run present — leave it alone in production.
PROC_PATTERN="${HALAL_SUPERVISOR_PROC_PATTERN:-python[0-9.]* .*run_paper_trading\.py}"

# The owner's canonical legacy launch flags (single-run mode only).
LEGACY_RUN_FLAGS=(
  --strategy donchian_1d
  --timeframe 1d
  --starting-usdt 1000
  --trade-usdt 100
  --poll-seconds 3600
  --daily-report-utc 18:00
  --weekly-report-day SUN
  --weekly-report-utc 12:00
)

MODE="loop"

usage() { sed -n '2,90p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

log() {
  local line
  line="$(date -u '+%Y-%m-%dT%H:%M:%SZ') [supervise${LABEL:+ $LABEL}] $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$SUP_LOG" 2>/dev/null || true
}

cmdline_of() { tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null; }

# PIDs of a live runner process (empty when none).
#   per-run mode: only processes carrying THIS label, never a supervisor.
#   legacy mode : any runner at all.
running_pids() {
  local pid cmd
  if [ -z "$LABEL" ]; then
    pgrep -f "$PROC_PATTERN" 2>/dev/null | tr '\n' ' '
    return 0
  fi
  for pid in $(pgrep -f "$PROC_PATTERN" 2>/dev/null); do
    cmd=$(cmdline_of "$pid") || continue
    case "$cmd" in
      *supervise_paper_run.sh*) continue ;;
      *"--run-label $LABEL"*) printf '%s\n' "$pid" ;;
    esac
  done
}

is_alive() {
  [ -n "$(running_pids | tr -d ' \n')" ]
}

# If more than one process is running this label, keep the oldest and stop the
# rest. Only ever signals processes of this label (legacy mode: only when the
# caller explicitly asks, which it does not).
dedupe() {
  local pids first rest pid
  [ -n "$LABEL" ] || return 0
  pids=$(running_pids | sort -n | tr '\n' ' ')
  pids=${pids% }
  [ -n "$pids" ] || return 0
  first=${pids%% *}
  rest=${pids#"$first"}
  for pid in $rest; do
    log "duplicate runner for $LABEL (pid $pid) — stopping it, keeping pid $first"
    kill -TERM "$pid" 2>/dev/null || true
  done
}

launch() {
  local flags
  if [ -n "$LABEL" ]; then
    flags="${RUN_ARGS[*]}"
  else
    flags="${LEGACY_RUN_FLAGS[*]}"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY RUN: would launch '$PYTHON $RUNNER $flags'"
    return 0
  fi
  log "launching: $PYTHON $RUNNER $flags"
  # setsid + nohup: survives this shell; output goes to the run's own log, so
  # the audit trail / Telegram behaviour is unchanged. Paper-only env is forced.
  # pushd/popd (not a `( ... & )` subshell) so no shell of ours stays behind
  # waiting on the run and holding its std streams open.
  pushd "$BACKEND_DIR" >/dev/null || { log "cannot cd $BACKEND_DIR"; return 1; }
  if [ -n "$LABEL" ]; then
    HALAL_TRADING_MODE=paper HALAL_LIVE_ENABLED=false \
      setsid nohup "$PYTHON" "$RUNNER" "${RUN_ARGS[@]}" >> "$RUN_LOG" 2>&1 < /dev/null &
  else
    HALAL_TRADING_MODE=paper HALAL_LIVE_ENABLED=false \
      setsid nohup "$PYTHON" "$RUNNER" "${LEGACY_RUN_FLAGS[@]}" >> "$RUN_LOG" 2>&1 < /dev/null &
  fi
  popd >/dev/null
  sleep 8
  if is_alive; then
    dedupe
    log "runner is up (pid $(running_pids | tr '\n' ' '))"
    return 0
  fi
  log "launch did not come up — see $RUN_LOG"
  return 1
}

check() {
  # 0 = nothing to do (alive or deliberately idle), 1 = a relaunch was attempted
  if [ -z "$LABEL" ] && [ ! -f "$ENV_FILE" ]; then
    log "no $ENV_FILE — nothing to supervise (no-op)"
    return 0
  fi
  if is_alive; then
    dedupe
    return 0
  fi
  log "paper run${LABEL:+ $LABEL} is NOT running — relaunching"
  launch
}

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --once) MODE="once"; shift ;;
      --status) MODE="status"; shift ;;
      --help|-h) usage; return 0 ;;
      --run-label) LABEL="${2:-}"; shift 2 || shift ;;
      --run-log) RUN_LOG="${2:-}"; shift 2 || shift ;;
      --interval) INTERVAL="${2:-}"; shift 2 || shift ;;
      --supervisor-log) SUP_LOG="${2:-}"; shift 2 || shift ;;
      --) shift; RUN_ARGS=("$@"); break ;;
      "") shift ;;
      *) printf 'unknown argument %s (try --help)\n' "$1" >&2; return 2 ;;
    esac
  done

  if [ -n "$LABEL" ] && [ ${#RUN_ARGS[@]} -eq 0 ]; then
    printf 'per-run mode needs the runner flags after -- (got none)\n' >&2
    return 2
  fi
  if [ -n "$LABEL" ]; then
    case " ${RUN_ARGS[*]} " in
      *" --run-label $LABEL "*) : ;;
      *) printf 'refusing: runner flags do not carry --run-label %s\n' "$LABEL" >&2; return 2 ;;
    esac
    : "${INTERVAL:=15}"
    # One supervisor per label: hold an exclusive lock for this process's life.
    exec 9>>"$SUP_LOG" || { printf 'cannot open %s\n' "$SUP_LOG" >&2; return 2; }
    if ! flock -n 9; then
      log "another supervisor already holds $SUP_LOG — exiting (never a second supervisor for one run)"
      return 0
    fi
  fi
  : "${INTERVAL:=60}"

  log "start | backend='$BACKEND_DIR' label='${LABEL:-<legacy>}' interval=${INTERVAL}s dry_run=$DRY_RUN mode=$MODE"
  if [ ! -f "$RUNNER" ]; then
    log "run_paper_trading.py not found at $RUNNER — refusing to do anything"
    return 2
  fi
  if [ ! -x "$PYTHON" ]; then
    log "interpreter not found at $PYTHON — refusing to do anything"
    return 2
  fi

  if [ "$MODE" = "status" ]; then
    if is_alive; then
      log "alive: $(running_pids | tr '\n' ' ')"
      return 0
    fi
    log "not running"
    return 1
  fi

  if [ -z "$LABEL" ] && [ ! -f "$ENV_FILE" ]; then
    log "no $ENV_FILE — nothing to supervise; exiting"
    return 0
  fi
  if is_alive; then
    log "paper run${LABEL:+ $LABEL} is already alive (pid $(running_pids | tr '\n' ' ')) — monitoring only, never a second instance"
    dedupe
  fi

  if [ "$MODE" = "once" ]; then
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
      dedupe
    fi
    sleep "$INTERVAL"
  done
}

main "$@"
