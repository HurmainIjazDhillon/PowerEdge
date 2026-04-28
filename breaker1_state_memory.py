"""
breaker1_state_memory.py
========================
Breaker 1 state tracking (mirrors Breaker 2)
"""

live_state = None


def update_live_state(state, source="telemetry"):
    """Update live state from any source immediately."""
    global live_state
    
    if state not in ("ON", "OFF"):
        return
    
    if live_state != state:
        live_state = state
        if source == "control":
            print(f"[B1-MEMORY][CONTROL] Live state -> {state}")
        elif source == "scheduler":
            print(f"[B1-MEMORY][SCHEDULER] Live state -> {state}")
        else:
            print(f"[B1-MEMORY] Live state -> {state}")


def get_live_state():
    """Get current live state."""
    return live_state