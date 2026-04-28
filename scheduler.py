"""
scheduler.py
============

Countdown Scheduler

Responsibilities:
- Persist countdown jobs to disk
- Execute jobs at the correct time
- Support multiple breakers independently
- Be restart-safe
- NO knowledge of MQTT
- NO guardian logic
"""

"""
scheduler.py
============

Countdown Scheduler

Responsibilities:
- Persist countdown jobs to disk
- Execute jobs at the correct time
- Support multiple breakers independently
- Be restart-safe
"""

import json
from datetime import datetime
from pathlib import Path
import telemetry
# --------------------------------------------------
# File where countdown jobs are persisted
SCHEDULE_FILE = Path("scheduled_commands.json")
# --------------------------------------------------
# COUNTDOWN DE-DUP MEMORY
# --------------------------------------------------
_last_scheduled_state = {}


# --------------------------------------------------
# LOAD PERSISTED JOBS FROM DISK
# --------------------------------------------------
def load_jobs():
    """
    Load previously scheduled countdown jobs.

    Returns:
        list: list of job dictionaries
    """
    if not SCHEDULE_FILE.exists():
        return []

    try:
        with open(SCHEDULE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


# --------------------------------------------------
# SAVE JOBS TO DISK
# --------------------------------------------------
def save_jobs(jobs):
    """
    Persist scheduler jobs to disk.
    Creates the file if it does not exist.
    """
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


# --------------------------------------------------
# PROCESS COUNTDOWN JOBS
# --------------------------------------------------
def process(jobs, device_map, config_map):
    """
    Execute due countdown jobs for ALL breakers.

    Args:
        jobs (list): scheduler job list
        device_map (dict): {asset_id: tinytuya device}
        config_map (dict): {asset_id: breaker config}
    """
    if not jobs:
        return

    now = datetime.now().astimezone()
    executed_any = False

    for job in jobs:
        if job.get("executed"):
            continue

        execute_at = datetime.fromisoformat(job["execute_at"])
        if now < execute_at:
            continue

        asset_id = job["asset_id"]
        cfg = config_map.get(asset_id)
        dev = device_map.get(asset_id)

        if not cfg or not dev:
            job["executed"] = True
            executed_any = True
            continue

        try:
            # ✅ REMOVED de-dup check - "executed" flag prevents re-runs
            
            # 1️⃣ APPLY SCHEDULED CONTROL
            dev.set_status(job["state"] == "ON", cfg["switch_dps"])
            
            # 🧠 SYNC INTERNAL STATE (CRITICAL FOR BREAKER 2)
            if cfg["name"] == "Breaker 2":
                from breaker2_state_memory import update_live_state
                update_live_state(job["state"])

            print(
                f"[COUNTDOWN][EXECUTED] {cfg['name']} -> {job['state']} "
                f"(scheduled at {job['execute_at']})"
            )

            # 🔥 FORCE IMMEDIATE TELEMETRY PUBLISH
            telemetry.force_publish = True

            job["executed"] = True
            executed_any = True

        except Exception as e:
            print(f"[COUNTDOWN][ERROR] {e}")

    if executed_any:
        save_jobs(jobs)
