# Crash supervisor for the 30-day paper run (best effort — NOT the guarantee)

Two files live here:

| File | What it is |
| --- | --- |
| `../supervise_paper_run.sh` | A watchdog loop: every 60 s it checks whether `run_paper_trading.py` is alive and relaunches it with the owner's exact flags if it is not. Never starts a second instance, no-ops when `backend/.env` is missing, logs to `backend/supervise_paper_run.log`, backs off exponentially on a run that dies instantly. |
| `halaltrade-paper-run.service` | A systemd unit with `Restart=always` that runs the paper run (Variant A), or the watchdog (Variant B). |

**Nothing here is installed or enabled.** These files exist so the owner can
approve and install them deliberately.

## The honest caveat — read this before relying on it

This machine has been replaced by the platform repeatedly, and each swap killed
the paper run. **A supervisor cannot fix that**: when the machine is replaced,
this unit and the watchdog are gone with it, and nothing restarts until a human
(or a fresh install on the new box) brings the run back.

The guarantee against machine swaps is in the runner, not here:

* `backend/run_paper_trading.py` persists the report-scheduler state
  (last sent daily/weekly slot) to `backend/paper_run_state.json` with atomic
  writes, and reloads it before the first report check;
* after a restart it delivers **at most one missed daily (18:00 UTC) and one
  missed weekly (SUN 12:00 UTC)** report, then resumes the normal schedule;
* a slot that was already reported is never sent twice — including when the new
  box's clock is *behind* the old one;
* a missing or corrupt state file is a warning plus empty state, never a crash.

So: the supervisor protects against a plain process crash **while the box
lives**. The persisted state + bounded catch-up protects against the swap.

## Installing (owner's decision — do not run this unattended)

```bash
# 1. Preconditions
ls -l /home/team/shared/halaltrade/backend/.env            # config present
ls -l /home/team/shared/halaltrade/backend/.venv/bin/python  # venv present
pgrep -af run_paper_trading.py                             # note any run already alive

# 2. Install the unit (Variant A) — adjust paths if the repo moves
sudo cp /home/team/shared/halaltrade/backend/deploy/halaltrade-paper-run.service \
        /etc/systemd/system/halaltrade-paper-run.service
sudo systemctl daemon-reload
sudo systemctl enable --now halaltrade-paper-run.service
systemctl status halaltrade-paper-run.service --no-pager

# 3. Follow the run
journalctl -u halaltrade-paper-run.service -f
tail -f /home/team/shared/halaltrade/backend/paper_run.log
```

To use the watchdog instead, comment out the `ExecStart=` python line in the
unit and uncomment the `ExecStart=.../supervise_paper_run.sh` line (Variants A
and B are mutually exclusive — two supervisors racing each other is exactly
what the "refuse a second instance" check is there to prevent).

Manual, systemd-free smoke check of the watchdog (does not launch anything):

```bash
HALAL_SUPERVISOR_DRY_RUN=1 backend/supervise_paper_run.sh --once
backend/supervise_paper_run.sh --status
```

## What it will never do

* never starts a second paper run while one is alive (`pgrep` on
  `python … run_paper_trading.py`);
* never edits, creates or invents `backend/.env`, and does nothing at all when
  it is absent;
* never changes strategy, size, symbol, poll cadence, report times, risk limits
  or any of the Shariah / Risk / Security / Execution gates — the flags passed
  through are the owner's original launch flags;
* never touches `halaltrade.db` directly, the API server, or the site — it only
  starts the same runner the owner starts by hand, which owns its own DB use;
* never enables live trading: the runner forces `trading_mode=paper` and
  `live_enabled=False`, and no live order path exists in the repo.
