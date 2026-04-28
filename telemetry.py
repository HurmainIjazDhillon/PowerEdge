"""
telemetry.py
============

Responsibilities:
- Read device DPS
- Build structured telemetry payloads
- Publish to OpenRemote data attributes
- NO control logic
- NO scheduler logic
"""

import json
from breaker2_state_memory import update_live_state, telemetry_lost
import time
import threading

force_publish = False

# --------------------------------------------------
# TIMING CONFIG (INDEPENDENT INTERVALS)
# --------------------------------------------------
DPS_FETCH_INTERVAL = 10      # 🔥 Fetch DPS every 10 seconds (CHANGEABLE)
PUBLISH_INTERVAL = 10        # Publish every 40 seconds (unchanged)


# --------------------------------------------------
# DPS CACHE (MODULE-LEVEL STATE)
# --------------------------------------------------
_breaker1_dps_cache = None
_breaker1_dps_timestamp = 0

_breaker2_dps_cache = None
_breaker2_dps_timestamp = 0
_breaker2_last_known_state = None


# --------------------------------------------------
# TIMEOUT WRAPPER FOR DEVICE STATUS CALLS
# --------------------------------------------------
def get_device_status(device, timeout_seconds=5, retries=1):
    """
    Call device.status() with timeout and retry logic.
    Returns status dict or raises TimeoutError if all retries fail.
    """
    last_exception = None
    
    for attempt in range(retries):
        result = [None]
        exception = [None]

        def _call_status():
            try:
                result[0] = device.status()
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=_call_status, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        # Check for exceptions from the thread
        if exception[0]:
            last_exception = exception[0]
            continue
        
        # Check if we got a result
        if result[0] is not None:
            return result[0]
        
        last_exception = TimeoutError(f"Device status timeout after {timeout_seconds}s")
    
    # All retries exhausted
    raise last_exception


# --------------------------------------------------
# BREAKER 1 OFFLINE CONTROL (MODULE-LEVEL STATE)
# --------------------------------------------------
_b1_offline = False
_b1_last_attempt = 0
_B1_BACKOFF_SECONDS = 5


def fetch_breaker1_dps(breaker_device):
    """
    🔥 ISOLATED: Fetch fresh DPS only. No payload building.
    Returns DPS dict or None if offline.
    """
    global _b1_offline, _b1_last_attempt

    now = time.time()

    # 🔒 Backoff when offline to prevent blocking storms
    if _b1_offline and (now - _b1_last_attempt) < _B1_BACKOFF_SECONDS:
        return None

    _b1_last_attempt = now

    try:
        data = get_device_status(breaker_device, timeout_seconds=5)
        dps = data.get("dps", {})

        # OFFLINE DETECTION
        if not dps:
            if not _b1_offline:
                print("[B1-TELEMETRY][OFFLINE] No DPS received")
            _b1_offline = True
            return None

        # ONLINE RECOVERY
        if _b1_offline:
            print("[B1-TELEMETRY][ONLINE] Telemetry restored")
        _b1_offline = False

        return dps

    except Exception as e:
        if not _b1_offline:
            print(f"[B1-TELEMETRY][ERROR] {e}")
        _b1_offline = True
        return None


def build_breaker1_payload(dps):
    """
    🔥 PURE: Build payload from cached DPS. No device calls.
    Returns payload dict or None.
    """
    if not dps:
        return None

    voltage_raw = dps.get("20")
    voltage = voltage_raw / 10 if voltage_raw and voltage_raw > 1000 else voltage_raw
    power_w = dps.get("19")/10
    energy_wh = (((power_w/60)/60)/1000)*PUBLISH_INTERVAL # Convert from Ws to Wh

    return {
        "Status": {
            "state": "ON" if dps.get("1", False) else "OFF",
            "relay_status": format_value(dps.get("38")),
            "online_state": format_value(dps.get("66"))
        },
        "Power_Metrics": {
            "power_W": format_value(power_w),
            "voltage_V": format_value(voltage),
            "current_mA": format_value(dps.get("18")),
            "energy_Wh": format_value(energy_wh)
        },
        # "Coefficients": {
        #     "voltage_coe": format_value(dps.get("22")),
        #     "current_coe": format_value(dps.get("23")),
        #     "power_coe": format_value(dps.get("24")),
        #     "energy_coe": format_value(dps.get("25"))
        # },
        "Settings": {
            "countdown_s": format_value(dps.get("9")),
            # "child_lock": format_value(dps.get("41")),
            # "light_mode": format_value(dps.get("40")),
            # "cycle_time": format_value(dps.get("42"))
        }
        # "Diagnostics": {
        #     "test_bit": format_value(dps.get("21")),
        #     "faults": format_value(dps.get("26")),
        #     "alarm_set_1": format_value(dps.get("48")),
        #     "alarm_set_2": format_value(dps.get("49"))
        # }
    }


# --------------------------------------------------
# BREAKER 2 OFFLINE DEBOUNCE
# --------------------------------------------------
_B2_EMPTY_DPS_LIMIT = 3
_B2_TIMEOUT_LIMIT = 2
_b2_empty_dps_count = 0
_b2_timeout_count = 0
_b2_last_reconnect = 0
_B2_RECONNECT_COOLDOWN = 10


def fetch_breaker2_dps(breaker_device):
    """
    🔥 ISOLATED: Fetch fresh DPS only. No payload building.
    Returns DPS dict or None if offline.
    """
    global _b2_empty_dps_count, _b2_timeout_count, _b2_last_reconnect
    global _breaker2_last_known_state  # 🔥 ADD THIS LINE

    now = time.time()

    # 🔧 AUTO-RECONNECT after persistent timeouts (with cooldown)
    if _b2_timeout_count >= _B2_TIMEOUT_LIMIT:
        if (now - _b2_last_reconnect) >= _B2_RECONNECT_COOLDOWN:
            if reconnect_if_needed(breaker_device, "B2"):
                _b2_last_reconnect = now
                _b2_timeout_count = 0
                _b2_empty_dps_count = 0
                time.sleep(5)
                print("[B2-TELEMETRY][RECONNECT] Skipping cycle, will retry next interval")
                return None

    try:
        data = get_device_status(breaker_device, timeout_seconds=5)
        dps = data.get("dps", {})

        # 🔥 DETECT PHYSICAL BUTTON CHANGE IMMEDIATELY
        if dps:
            switch_state = dps.get("16", False)
            current_state = "ON" if switch_state else "OFF"
            
            if _breaker2_last_known_state is not None and _breaker2_last_known_state != current_state:
                print(f"[B2-TELEMETRY] Physical change: {_breaker2_last_known_state} → {current_state}")
                from breaker2_state_memory import update_live_state
                update_live_state(current_state, source="physical")
                force_publish = True
            
            _breaker2_last_known_state = current_state

        # OFFLINE DEBOUNCE (HARD STOP AFTER CONFIRM)
        if not dps:
            _b2_empty_dps_count += 1
            _b2_timeout_count = 0

            if _b2_empty_dps_count <= _B2_EMPTY_DPS_LIMIT:
                print(f"[B2-TELEMETRY][WARN] Empty DPS ({_b2_empty_dps_count}/{_B2_EMPTY_DPS_LIMIT})")

            if _b2_empty_dps_count == _B2_EMPTY_DPS_LIMIT:
                print("[B2-TELEMETRY][OFFLINE] DPS empty (confirmed)")
                telemetry_lost()

            return None

        # DPS RECOVERED → RESET DEBOUNCE
        if _b2_empty_dps_count != 0 or _b2_timeout_count != 0:
            _b2_empty_dps_count = 0
            _b2_timeout_count = 0
            print("[B2-TELEMETRY][ONLINE] Connection restored")

        return dps

    except TimeoutError as e:
        print(f"[B2-TELEMETRY][TIMEOUT] {e} ({_b2_timeout_count + 1}/{_B2_TIMEOUT_LIMIT})")
        _b2_timeout_count += 1
        
        if _b2_timeout_count >= _B2_TIMEOUT_LIMIT:
            print("[B2-TELEMETRY][OFFLINE] Multiple timeouts (confirmed)")
            telemetry_lost()
            _b2_empty_dps_count = _B2_EMPTY_DPS_LIMIT
        
        return None

    except Exception as e:
        print(f"[B2-TELEMETRY][ERROR] {e}")
        _b2_empty_dps_count += 1
        _b2_timeout_count = 0

        if _b2_empty_dps_count == _B2_EMPTY_DPS_LIMIT:
            print("[B2-TELEMETRY][OFFLINE] DPS error (confirmed)")
            telemetry_lost()

        return None


def build_breaker2_payload(dps):
    """
    🔥 PURE: Build payload from cached DPS. No device calls.
    Returns payload dict or None.
    """
    if not dps:
        return None

    total_energy_kwh = dps.get("1", 0) / 100
    balance_energy_kwh = dps.get("13", 0) / 10
    voltage_v = dps.get("116", 0) / 10
    frequency_hz = dps.get("105", 0) / 10
    power_factor = dps.get("104", 0) / 1000
    switch_state = dps.get("16", False)

    state = "ON" if switch_state else "OFF"
    update_live_state(state)

    leakage_current = dps.get("15", 0)
    residual_current = dps.get("117", 0)

    current_ma = 0
    power_w = 0

    for key in ["2", "3", "4", "5", "6", "7", "8"]:
        if key in dps:
            val = dps[key]
            if 0 <= abs(val) <= 100:
                current_ma = val
            elif 0 <= abs(val) <= 10000:
                power_w = val / 1000

    return {
        "Status": {
            "state": state,
            "switch": format_value(switch_state),
            "prepayment_enabled": format_value(dps.get("11"))
        },
        "Power_Metrics": {
            "voltage_V": format_value(voltage_v),
            "current_mA": format_value(dps.get("117", 0)),
            "power_W": format_value(power_w),
            "power_factor": format_value(power_factor),
            "frequency_hz": format_value(frequency_hz),
            "total_energy_kwh": format_value(total_energy_kwh),
            "breaker_number": format_value(dps.get("19"))
        },
        "Energy_Management": {
            "balance_energy_kwh": format_value(balance_energy_kwh)
        },
        "Safety": {
            "leakage_current_mA": format_value(leakage_current),
            "residual_current_mA": format_value(residual_current),
            "leakage_test": format_value(dps.get("21")),
            "fault_bitmap": format_value(dps.get("9"))
        },
        "Diagnostics": {
            "param_118": format_value(dps.get("118")),
            "raw_dps_count": format_value(len(dps))
        }
    }


# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def format_value(v, default="0"):
    return default if v is None else str(v)


def reconnect_if_needed(device, device_name):
    """Attempt to reconnect device if status calls are failing."""
    try:
        device.heartbeat()
        return False
    except Exception:
        print(f"[{device_name}][RECONNECT] Attempting reconnection...")
        try:
            device.close()
            time.sleep(2)
            print(f"[{device_name}][RECONNECT] Connection reset")
            return True
        except Exception as e:
            print(f"[{device_name}][RECONNECT][FAILED] {e}")
            return False


# --------------------------------------------------
# PUBLISH TELEMETRY (USES CACHED DPS)
# --------------------------------------------------
def publish_telemetry(devices, configs, mqtt_client, b1_dps, b2_dps):
    """
    Publish telemetry using cached DPS (no new device calls).
    """
    for asset_id, device in devices.items():
        cfg = configs.get(asset_id)
        if not cfg:
            continue

        if cfg["name"] == "Breaker 1":
            payload = build_breaker1_payload(b1_dps)
        elif cfg["name"] == "Breaker 2":
            payload = build_breaker2_payload(b2_dps)
        else:
            continue

        if not payload:
            continue

        topic = (
            f"professorshospital/Smart_Breakers/"
            f"writeattributevalue/data/{asset_id}"
        )

        mqtt_client.publish(topic, json.dumps(payload), qos=1)
        print(f"[DATA] Published telemetry -> {cfg['name']}")

        global force_publish
        force_publish = False
