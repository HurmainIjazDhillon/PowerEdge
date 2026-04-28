"""
breaker1_memory.py
==================

Breaker 1 Relay Memory Handler

Responsibility:
- Configure relay startup behavior (DPS 38)
- Triggered ONLY from OpenRemote `last_state`
- Stateless, pure action module

Accepted values (case-insensitive):
- "ON"
- "OFF"
- "MEMORY"
"""

def set_relay_memory(device, cfg, mode):
    """
    Set relay startup memory mode for Breaker 1.

    Parameters:
    - device : tinytuya device instance
    - cfg    : breaker configuration dict
    - mode   : "ON", "OFF", or "MEMORY" (any case)
    """

    if not device:
        print("[B1-MEMORY][WARN] Device not available")
        return

    if not mode:
        print("[B1-MEMORY][WARN] No memory mode provided")
        return

    if not isinstance(mode, str):
        print(f"[B1-MEMORY][WARN] Invalid memory type ignored: {mode}")
        return

    # Normalize input (OpenRemote may send any case)
    mode = mode.strip().upper()

    value_map = {
        "ON": "on",
        "OFF": "off",
        "MEMORY": "memory"
    }

    if mode not in value_map:
        print(f"[B1-MEMORY][WARN] Invalid memory mode ignored: {mode}")
        return

    try:
        device.set_value(cfg["relay_status_dps"], value_map[mode])
        print(f"[B1-MEMORY] Relay startup mode set -> {mode}")
    except Exception as e:
        print(f"[B1-MEMORY][ERROR] Failed to set relay memory: {e}")
