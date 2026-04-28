"""
mqtt_handler.py
===============

Responsibilities:
- Parse MQTT messages
- STRICT separation of CONTROL / COUNTDOWN / LAST_STATE
- Countdown logic is IDENTICAL for all breakers
- Countdown NEVER toggles immediately
- NO scheduler execution
- NO guardian enforcement
"""

import json
from datetime import datetime, timedelta
import telemetry


# 🔥 ONLY NEW IMPORT (SAFE)
from breaker2_state_memory import update_live_state
# --------------------------------------------------
# CONTROL DE-DUPLICATION MEMORY (PER ASSET)
# --------------------------------------------------
_last_control_state = {}


def handle_message(
    msg,
    device_map,
    config_map,
    scheduled_jobs,
    breaker1_memory_fn,
    breaker2_guardian
):
    # --------------------------------------------------
    # PARSE JSON PAYLOAD
    # --------------------------------------------------
    try:
        payload = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"[MQTT][ERROR] Invalid JSON: {e}")
        return

    # Guard against null / non-dict payloads
    if not isinstance(payload, dict):
        print(f"[MQTT][WARN] Payload is not an object: {payload}")
        return

    topic = msg.topic
    asset_id = topic.split("/")[-1]

    cfg = config_map.get(asset_id)
    dev = device_map.get(asset_id)

    if not cfg or not dev:
        print(f"[MQTT][WARN] Unknown asset_id: {asset_id}")
        return

    # ==================================================
    # LAST_STATE
    # Topic: attributevalue/last_state/<asset_id>
    # ==================================================
    if "/attributevalue/last_state/" in topic:
        last_state = payload.get("last_state")

        if not isinstance(last_state, str):
            print(f"[LAST_STATE][WARN] Invalid value type: {last_state}")
            return

        last_state = last_state.upper()

        if last_state not in ("ON", "OFF", "MEMORY"):
            print(f"[LAST_STATE][WARN] Invalid value: {last_state}")
            return

        # --------------------------------------------------
        # BREAKER 2: ignore MQTT echo (telemetry-driven)
        # --------------------------------------------------
        if cfg["name"] == "Breaker 2":
            if breaker2_guardian:
                breaker2_guardian.update_from_last_state(last_state)
                print(f"[LAST_STATE] Breaker 2 -> {last_state}")
            else:
                print(f"[LAST_STATE][IGNORED] Breaker 2 echo -> {last_state}")

        # --------------------------------------------------
        # BREAKER 1: apply memory logic (control-driven)
        # --------------------------------------------------
        else:
            breaker1_memory_fn(dev, cfg, last_state)
            print(f"[LAST_STATE] Breaker 1 memory -> {last_state}")

        return

    # ==================================================
    # COUNTDOWN (STRICT & IDENTICAL FOR BOTH BREAKERS)
    # Topic: attributevalue/countdown/<asset_id>
    # ==================================================
    if "/attributevalue/countdown/" in topic:
        state = payload.get("state")
        countdown = payload.get("countdown") or payload.get("contudown")

        if not state or not countdown:
            print("[COUNTDOWN][WARN] Missing state or countdown")
            return

        if not isinstance(state, str):
            print(f"[COUNTDOWN][WARN] Invalid state type: {state}")
            return

        state = state.upper()
        if state not in ("ON", "OFF"):
            print(f"[COUNTDOWN][WARN] Invalid state: {state}")
            return

        try:
            minutes = int(countdown.split()[0])
        except Exception:
            print(f"[COUNTDOWN][ERROR] Invalid format: {countdown}")
            return

        execute_at = (
            datetime.now().astimezone()
            + timedelta(minutes=minutes)
        ).isoformat()

        scheduled_jobs.append({
            "asset_id": asset_id,
            "state": state,
            "execute_at": execute_at,
            "executed": False
        })

        print(
            f"[COUNTDOWN][SCHEDULED] {cfg['name']} -> {state} "
            f"in {minutes} min (exec @ {execute_at})"
        )
        return

    # ==================================================
    # CONTROL (IMMEDIATE ONLY)
    # Topic: attributevalue/control/<asset_id>
    # ==================================================
    if "/attributevalue/control/" in topic:
        state = payload.get("state")

        if not isinstance(state, str):
            print("[CONTROL][WARN] Missing or invalid state")
            return

        state = state.upper()
        if state not in ("ON", "OFF"):
            print(f"[CONTROL][WARN] Invalid state: {state}")
            return

        try:
            # --------------------------------------------------
            # ✅ FETCH REAL-TIME STATUS FROM TUYA
            # --------------------------------------------------
            status_data = dev.status()
            
            dps_id = cfg["switch_dps"]
            current_dps_value = status_data.get("dps", {}).get(str(dps_id))
            current_state = "ON" if current_dps_value else "OFF"
            
            print(f"[CONTROL] {cfg['name']} DPS{dps_id} current: {current_state}, desired: {state}")
            
            # IGNORE if already in desired state
            if current_state == state:
                print(f"[CONTROL][IGNORED] {cfg['name']} already {state}")
                return
            
            # --------------------------------------------------
            # ✅ APPLY CONTROL (only if state differs)
            # --------------------------------------------------
            dev.set_status(state == "ON", dps_id)
            print(f"[CONTROL] {cfg['name']} -> {state}")
            
            # 🧠 SYNC INTERNAL STATE
            dev.state = state

            # 🔐 UPDATE DE-DUP MEMORY
            _last_control_state[asset_id] = state

            if cfg["name"] == "Breaker 2":
                update_live_state(state, source="control")

        except Exception as e:
            print(f"[CONTROL][ERROR] {e}")

        return