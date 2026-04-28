"""
breaker2_state_memory.py
========================
Breaker 2 software-only state memory (STABLE + CORRECT)
"""

import time
import telemetry

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------
RESTORE_DELAY_SECONDS = 10

# --------------------------------------------------
# STATE VARIABLES
# --------------------------------------------------
live_state = None
last_state = None
offline = False
restore_pending = False
restore_started_at = None
_last_state_updated = False


# --------------------------------------------------
# UNIVERSAL STATE UPDATE (ALL SOURCES)
# --------------------------------------------------
def update_live_state(state, source="telemetry"):
    """
    Updates live_state from ANY source immediately.

    Args:
        state: "ON" or "OFF"
        source: "telemetry", "control", "scheduler", "physical"
    """
    global live_state, offline, restore_pending, restore_started_at

    if state not in ("ON", "OFF"):
        return

    # Device came back online
    if offline:
        offline = False
        print("[B2-MEMORY][ONLINE] Telemetry restored")

    # Control/Scheduler CANCELS restore
    if source in ("control", "scheduler"):
        restore_pending = False
        restore_started_at = None

    # Update live state immediately
    if live_state != state:
        live_state = state
        if source == "control":
            print(f"[B2-MEMORY][CONTROL] Live state -> {state}")
        elif source == "scheduler":
            print(f"[B2-MEMORY][SCHEDULER] Live state -> {state}")
        elif source == "physical":
            print(f"[B2-MEMORY][PHYSICAL] Live state -> {state}")
        else:
            print(f"[B2-MEMORY] Live state -> {state}")


# --------------------------------------------------
# OFFLINE DETECTION
# --------------------------------------------------
def telemetry_lost():
    global offline, last_state, live_state, restore_pending, restore_started_at, _last_state_updated

    if offline:
        return

    offline = True

    if live_state is None:
        print("[B2-MEMORY][OFFLINE] No live state to store")
        return

    last_state = live_state
    restore_pending = True
    restore_started_at = None
    _last_state_updated = True

    print(f"[B2-MEMORY][OFFLINE] Last state saved -> {last_state}")


# --------------------------------------------------
# LAST STATE PUBLISH (ONE SHOT)
# --------------------------------------------------
def consume_last_state_update():
    global _last_state_updated

    if not _last_state_updated:
        return None

    _last_state_updated = False
    return last_state


# --------------------------------------------------
# RESTORE LOGIC
# --------------------------------------------------
def should_restore_last_state():
    global restore_started_at

    if not restore_pending or last_state is None:
        return False

    if restore_started_at is None:
        restore_started_at = time.time()
        print(f"[B2-RESTORE] Waiting {RESTORE_DELAY_SECONDS}s before restore")
        return False

    return (time.time() - restore_started_at) >= RESTORE_DELAY_SECONDS


def consume_restore():
    global restore_pending, restore_started_at, live_state

    restore_pending = False
    restore_started_at = None
    live_state = last_state

    print(f"[B2-RESTORE] Applied last state -> {last_state}")
    telemetry.force_publish = True

    return last_state


# --------------------------------------------------
# READ-ONLY
# --------------------------------------------------
def get_last_state():
    return last_state
