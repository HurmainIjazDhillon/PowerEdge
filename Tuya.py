"""
Tuya.py
=======
MAIN RUNNER / ORCHESTRATOR

Responsibilities:
- Initialize system components
- Wire MQTT callbacks
- Run scheduler loop
- Publish telemetry on timer

NO business logic
NO DPS logic
NO guardian logic
"""

import time
import ssl
import threading
import paho.mqtt.client as mqtt
import telemetry
import json


# ============================================================
# IMPORT FUNCTIONAL MODULES
# ============================================================

from devices import get_devices, get_device_configs
from scheduler import load_jobs, process as process_schedules
from breaker1_memory import set_relay_memory
from mqtt_handler import handle_message
from telemetry import (
    fetch_breaker1_dps, fetch_breaker2_dps,
    build_breaker1_payload, build_breaker2_payload,
    DPS_FETCH_INTERVAL, PUBLISH_INTERVAL
)
from breaker2_state_memory import (
    should_restore_last_state, consume_restore,
    consume_last_state_update
)


# ============================================================
# SYSTEM STARTUP
# ============================================================

print("[SYSTEM] Starting Tuya Controller (MAIN)")

BREAKER2_DISABLED = True  # ← Control flag to disable Breaker 2

configs = get_device_configs()

# Remove Breaker 2 config if disabled (prevents connection attempt)
if BREAKER2_DISABLED:
    breaker2_asset_id = None
    for asset_id, cfg in list(configs.items()):
        if cfg["name"] == "Breaker 2":
            breaker2_asset_id = asset_id
            configs.pop(asset_id)
            print(f"[INIT] ⛔ Removed Breaker 2 config (DISABLED)")
            break

devices = get_devices(config_map=configs)  # Now only connects to enabled breakers

scheduled_jobs = load_jobs()

print("[SYSTEM] Devices initialized")

# ============================================================
# MQTT CLIENT (NETWORK THREAD ONLY)
# ============================================================

mqtt_client = mqtt.Client(
    client_id="Smart_Breakers",
    protocol=mqtt.MQTTv311,
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2
)

mqtt_client.username_pw_set(
    "professorshospital:professorshospital",
    "pylHUMjla5jMXld7jJTMOj88vvb0gZT7"
)

mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
mqtt_client.tls_insecure_set(True)

# Stable reconnect behavior
mqtt_client.reconnect_delay_set(min_delay=5, max_delay=30)

# ============================================================
# MQTT CALLBACKS
# ============================================================

mqtt_subscribed = False


def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_subscribed

    if reason_code != 0:
        print(f"[MQTT][ERROR] Connection failed: {reason_code}")
        return

    print("[MQTT] Connected successfully")

    if mqtt_subscribed:
        print("[MQTT] Already subscribed, skipping")
        return

    base = "professorshospital/Smart_Breakers/attributevalue"

    for asset_id, cfg in configs.items():
        # Skip Breaker 2 if disabled
        if BREAKER2_DISABLED and cfg["name"] == "Breaker 2":
            print(f"[MQTT] ⛔ Skipping subscription for {cfg['name']} (DISABLED)")
            continue
        
        client.subscribe(f"{base}/control/{asset_id}", qos=1)
        client.subscribe(f"{base}/last_state/{asset_id}", qos=1)
        client.subscribe(f"{base}/countdown/{asset_id}", qos=1)
        print(f"[MQTT] Subscribed -> {cfg['name']}")

    mqtt_subscribed = True


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    global mqtt_subscribed
    print(
        f"[MQTT] Disconnected | reason={reason_code} | "
        f"from_broker={disconnect_flags.is_disconnect_packet_from_server}"
    )
    # Force resubscribe on next reconnect
    mqtt_subscribed = False


def on_message(client, userdata, msg):
    try:
        print(f"[MQTT][RX] {msg.topic} -> {msg.payload.decode()}")

        # Skip if Breaker 2 is disabled and message contains Breaker 2
        if BREAKER2_DISABLED and "Breaker 2" in str(configs):
            for asset_id, cfg in configs.items():
                if cfg["name"] == "Breaker 2" and asset_id in msg.topic:
                    print(f"[MQTT] ⛔ Ignoring Breaker 2 message (DISABLED)")
                    return

        handle_message(
            msg=msg,
            device_map=devices,
            config_map=configs,
            scheduled_jobs=scheduled_jobs,
            breaker1_memory_fn=set_relay_memory,
            breaker2_guardian=None  # 🚫 guardian removed
        )


    except Exception as e:
        # Prevent MQTT thread crash
        print(f"[MQTT][ERROR] on_message exception: {e}")


mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

# ============================================================
# WORKER THREAD (TUYA I/O + SCHEDULER + TELEMETRY)
# ============================================================

def worker_loop():
    print("[WORKER] Tuya worker thread started")

    while True:
        now = time.time()

        # 1️⃣ Countdown scheduler (Breaker 1 + Breaker 2)
        process_schedules(scheduled_jobs, devices, configs)

        time.sleep(1)


# ============================================================
# 2️⃣ BREAKER 1 TELEMETRY LOOP (SEPARATE DPS FETCH)
# ============================================================

def breaker1_telemetry_loop():
    print("[B1-THREAD] Telemetry thread started")
    print(f"[B1-THREAD] DPS fetch interval: {DPS_FETCH_INTERVAL}s")
    print(f"[B1-THREAD] Publish interval: {PUBLISH_INTERVAL}s")

    last_dps_fetch = 0
    last_publish = 0
    breaker1_dps = None

    while True:
        now = time.time()

        # --------------------------------------------------
        # 🔥 FETCH DPS (INDEPENDENT INTERVAL)
        # --------------------------------------------------
        if (now - last_dps_fetch) >= DPS_FETCH_INTERVAL:
            for asset_id, device in devices.items():
                cfg = configs.get(asset_id)
                if not cfg or cfg["name"] != "Breaker 1":
                    continue

                breaker1_dps = fetch_breaker1_dps(device)
                last_dps_fetch = now

                if breaker1_dps:
                    print(
                        f"[B1-THREAD] ✅ Fresh DPS fetched "
                        f"(next in {DPS_FETCH_INTERVAL}s)"
                    )
                else:
                    print(
                        f"[B1-THREAD] ⚠️  DPS fetch failed (offline)"
                    )

                break  # only one Breaker 1 exists

        # --------------------------------------------------
        # 🔥 PUBLISH (SEPARATE INTERVAL - USE CACHED DPS)
        # --------------------------------------------------
        should_publish = (
            telemetry.force_publish or
            (now - last_publish) >= PUBLISH_INTERVAL
        )

        if should_publish and breaker1_dps:
            payload = build_breaker1_payload(breaker1_dps)

            if payload:
                for asset_id, device in devices.items():
                    cfg = configs.get(asset_id)
                    if not cfg or cfg["name"] != "Breaker 1":
                        continue

                    topic = (
                        f"professorshospital/Smart_Breakers/"
                        f"writeattributevalue/data/{asset_id}"
                    )

                    mqtt_client.publish(
                        topic, json.dumps(payload), qos=1
                    )
                    print("[DATA] Published telemetry -> Breaker 1")

                    last_publish = time.time()
                    telemetry.force_publish = False

                    break  # only one Breaker 1 exists

        time.sleep(0.1)


# ============================================================
# 2️⃣ BREAKER 2 TELEMETRY LOOP (SEPARATE DPS FETCH)
# ============================================================

def breaker2_telemetry_loop():
    print("[B2-THREAD] Telemetry thread started")
    
    if BREAKER2_DISABLED:
        print("[B2-THREAD] ⛔ Breaker 2 is DISABLED - idling indefinitely")
        while True:
            time.sleep(1)
        return
    
    print(f"[B2-THREAD] DPS fetch interval: {DPS_FETCH_INTERVAL}s")
    print(f"[B2-THREAD] Publish interval: {PUBLISH_INTERVAL}s")

    last_dps_fetch = 0
    last_publish = 0
    breaker2_dps = None
    restore_hold_until = 0  # ← ADD THIS LINE

    while True:
        now = time.time()

        # --------------------------------------------------
        # 🔥 FETCH DPS (INDEPENDENT INTERVAL)
        # --------------------------------------------------
        if (now - last_dps_fetch) >= DPS_FETCH_INTERVAL:
            # Skip fetch if still in restore hold-off
            if now < restore_hold_until:
                print(f"[B2-THREAD] ⏸️  Skipping DPS fetch (restore hold-off)")
                time.sleep(0.1)
                continue
            
            for asset_id, device in devices.items():
                cfg = configs.get(asset_id)
                if not cfg or cfg["name"] != "Breaker 2":
                    continue

                breaker2_dps = fetch_breaker2_dps(device)
                last_dps_fetch = now

                if breaker2_dps:
                    print(
                        f"[B2-THREAD] ✅ Fresh DPS fetched "
                        f"(next in {DPS_FETCH_INTERVAL}s)"
                    )
                else:
                    print(
                        f"[B2-THREAD] ⚠️  DPS fetch failed (offline)"
                    )

                break  # only one Breaker 2 exists

        # --------------------------------------------------
        # 1️⃣ PUBLISH last_state (ONE-SHOT, OFFLINE EVENT)
        #     ✅ do this BEFORE any early-continue
        # --------------------------------------------------
        last_state = consume_last_state_update()
        if last_state is not None:
            for asset_id in devices:
                cfg = configs.get(asset_id)
                if not cfg or cfg["name"] != "Breaker 2":
                    continue

                topic = (
                    f"professorshospital/Smart_Breakers/"
                    f"writeattributevalue/last_state/{asset_id}"
                )
                mqtt_client.publish(
                    topic,
                    json.dumps({"last_state": last_state}),
                    qos=1
                )
                print(f"[DATA] Published last_state -> {last_state}")
                break

        # --------------------------------------------------
        # 2️⃣ APPLY RESTORE ASAP (ONLY WHEN DPS IS BACK)
        # --------------------------------------------------
        if breaker2_dps and should_restore_last_state():
            state = consume_restore()

            for asset_id, device in devices.items():
                cfg = configs.get(asset_id)
                if not cfg or cfg["name"] != "Breaker 2":
                    continue

                device.set_status(state == "ON", 16)
                print(f"[CONTROL][RESTORE] Breaker 2 -> {state}")
                restore_hold_until = now + 3  # ← ADD THIS LINE (was: break)
                break

            time.sleep(0.1)
            continue

        # --------------------------------------------------
        # 🔥 PUBLISH (SEPARATE INTERVAL - USE CACHED DPS)
        # --------------------------------------------------
        should_publish = (
            telemetry.force_publish or
            (now - last_publish) >= PUBLISH_INTERVAL
        )

        if not should_publish or not breaker2_dps:
            time.sleep(0.1)
            continue

        # --------------------------------------------------
        # 3️⃣ IF OFFLINE → SKIP RESTORE & NORMAL PUBLISH
        # --------------------------------------------------
        if breaker2_dps is None:
            time.sleep(0.1)
            continue

        # --------------------------------------------------
        # 4️⃣ NORMAL TELEMETRY PUBLISH (USE CACHED DPS)
        # --------------------------------------------------
        payload = build_breaker2_payload(breaker2_dps)

        if payload:
            for asset_id in devices:
                cfg = configs.get(asset_id)
                if not cfg or cfg["name"] != "Breaker 2":
                    continue

                topic = (
                    f"professorshospital/Smart_Breakers/"
                    f"writeattributevalue/data/{asset_id}"
                )

                mqtt_client.publish(
                    topic, json.dumps(payload), qos=1
                )
                print("[DATA] Published telemetry -> Breaker 2")

                last_publish = time.time()
                telemetry.force_publish = False

                break  # only one Breaker 2 exists

        time.sleep(0.1)


# ============================================================
# START SYSTEM
# ============================================================

print("[MQTT] Connecting to broker...")
mqtt_client.connect("109.176.197.144", 8883, keepalive=20)
mqtt_client.loop_start()   # ✅ correct usage


# ------------------------------------------------------------
# START TELEMETRY THREADS (INDEPENDENT)
# ------------------------------------------------------------

threading.Thread(
    target=breaker1_telemetry_loop,
    daemon=True
).start()

if not BREAKER2_DISABLED:
    threading.Thread(
        target=breaker2_telemetry_loop,
        daemon=True
    ).start()


# ------------------------------------------------------------
# START SCHEDULER / WORKER THREAD
# ------------------------------------------------------------

threading.Thread(
    target=worker_loop,
    daemon=True
).start()

print("[SYSTEM] Controller running")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n[SYSTEM] Shutdown requested")
finally:
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    print("[SYSTEM] MQTT disconnected, system stopped")
