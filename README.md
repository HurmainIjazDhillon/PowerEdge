# ⚡ PowerEdge

> **Real-time IoT edge service** that bridges Tuya smart circuit breakers to the [OpenRemote](https://openremote.io/) platform over MQTT — delivering remote control, live telemetry, scheduled countdowns, and autonomous state recovery at the electrical edge.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Module Reference](#module-reference)
- [Data Flow](#data-flow)
- [Telemetry Payloads](#telemetry-payloads)
- [MQTT Topic Structure](#mqtt-topic-structure)
- [Configuration](#configuration)
- [Scheduling & Countdowns](#scheduling--countdowns)
- [State Memory & Recovery](#state-memory--recovery)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running the Controller](#running-the-controller)
- [Operational Notes](#operational-notes)

---

## Overview

**PowerEdge** is a Python-based IoT edge service that acts as a bidirectional bridge between Tuya-protocol smart circuit breakers and the OpenRemote IoT platform. It:

1. **Reads** real-time DPS (Data Point Status) from Tuya breakers over the local network.
2. **Publishes** structured telemetry (voltage, current, power, energy, safety metrics) to OpenRemote via MQTT.
3. **Receives** control commands, countdown schedules, and relay-memory configurations from OpenRemote.
4. **Maintains** persistent state memory so breakers can be autonomously restored to their last-known state after a power outage or network interruption.

The system currently manages **two breaker devices** (Breaker 1 and Breaker 2), each with independent telemetry loops, DPS fetch intervals, and state-management logic. Breaker 2 can be disabled via a single flag.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        Tuya.py (Orchestrator)                  │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  B1 Telemetry│  │  B2 Telemetry│  │   Worker (Scheduler) │  │
│  │    Thread     │  │    Thread     │  │      Thread          │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                      │              │
│         ▼                 ▼                      ▼              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              telemetry.py  (DPS Fetch + Payload Build)  │   │
│  └─────────────────────────────────────────────────────────┘   │
│         │                 │                                     │
│         ▼                 ▼                                     │
│  ┌─────────────┐  ┌─────────────────────┐                      │
│  │ devices.py  │  │ breaker*_memory.py  │                      │
│  │ (TinyTuya)  │  │ (State Tracking)    │                      │
│  └──────┬──────┘  └─────────────────────┘                      │
│         │                                                      │
└─────────┼──────────────────────────────────────────────────────┘
          │  LAN (Tuya Protocol 3.4 / 3.5)
          ▼
   ┌──────────────┐          MQTT (TLS 8883)         ┌──────────────┐
   │  Tuya Smart  │◄───────────────────────────────►│  OpenRemote  │
   │  Breakers    │                                  │   Platform   │
   └──────────────┘                                  └──────────────┘
```

### Threading Model

| Thread | Purpose | Loop Rate |
|---|---|---|
| **MQTT Network** | `paho-mqtt` `loop_start()` — handles connect/disconnect/message callbacks | Event-driven |
| **B1 Telemetry** | Fetches Breaker 1 DPS and publishes telemetry payloads | DPS fetch: 10 s / Publish: 10 s |
| **B2 Telemetry** | Fetches Breaker 2 DPS, handles state restore & `last_state` publish | DPS fetch: 10 s / Publish: 10 s |
| **Worker** | Executes due countdown/scheduled jobs | 1 s tick |

---

## Module Reference

| File | Role | Key Exports |
|---|---|---|
| [Tuya.py](Tuya.py) | **Main orchestrator** — wires MQTT, spawns threads, runs forever | Entry point |
| [devices.py](devices.py) | Stores breaker configs, initialises `tinytuya.OutletDevice` objects | `get_devices()`, `get_device_configs()` |
| [telemetry.py](telemetry.py) | DPS fetch (with timeout/retry), payload construction, reconnect logic | `fetch_breaker1_dps()`, `fetch_breaker2_dps()`, `build_breaker1_payload()`, `build_breaker2_payload()` |
| [mqtt_handler.py](mqtt_handler.py) | Parses inbound MQTT messages and dispatches to control/countdown/last_state handlers | `handle_message()` |
| [scheduler.py](scheduler.py) | Persists countdown jobs to `scheduled_commands.json`, executes them when due | `load_jobs()`, `process()`, `save_jobs()` |
| [breaker1_memory.py](breaker1_memory.py) | Sets Breaker 1 relay startup mode (DPS 38): `ON` / `OFF` / `MEMORY` | `set_relay_memory()` |
| [breaker1_state_memory.py](breaker1_state_memory.py) | Tracks Breaker 1 live ON/OFF state in-memory | `update_live_state()`, `get_live_state()` |
| [breaker2_state_memory.py](breaker2_state_memory.py) | Full state machine for Breaker 2: live state, offline detection, delayed restore | `update_live_state()`, `telemetry_lost()`, `should_restore_last_state()`, `consume_restore()` |

---

## Data Flow

### Telemetry (Outbound → OpenRemote)

```
Tuya Breaker ──LAN──► fetch_breaker*_dps() ──► build_breaker*_payload() ──► MQTT publish
                        (with timeout/retry)       (structured JSON)         (QoS 1)
```

### Control (Inbound ← OpenRemote)

```
OpenRemote ──MQTT──► on_message() ──► handle_message()
                                          │
                              ┌───────────┼────────────┐
                              ▼           ▼            ▼
                          CONTROL     COUNTDOWN    LAST_STATE
                        (immediate)  (scheduled)  (relay memory)
```

---

## Telemetry Payloads

### Breaker 1

```json
{
  "Status": {
    "state": "ON",
    "relay_status": "memory",
    "online_state": "1"
  },
  "Power_Metrics": {
    "power_W": "150.3",
    "voltage_V": "228.5",
    "current_mA": "650",
    "energy_Wh": "0.00042"
  },
  "Settings": {
    "countdown_s": "0"
  }
}
```

### Breaker 2

```json
{
  "Status": {
    "state": "ON",
    "switch": "True",
    "prepayment_enabled": "0"
  },
  "Power_Metrics": {
    "voltage_V": "230.1",
    "current_mA": "480",
    "power_W": "0.11",
    "power_factor": "0.98",
    "frequency_hz": "50.0",
    "total_energy_kwh": "12.45",
    "breaker_number": "1"
  },
  "Energy_Management": {
    "balance_energy_kwh": "50.0"
  },
  "Safety": {
    "leakage_current_mA": "0",
    "residual_current_mA": "0",
    "leakage_test": "0",
    "fault_bitmap": "0"
  },
  "Diagnostics": {
    "param_118": "0",
    "raw_dps_count": "15"
  }
}
```

---

## MQTT Topic Structure

All topics are prefixed with the realm and agent path:

```
professorshospital/Smart_Breakers/
```

### Subscriptions (Inbound)

| Topic Pattern | Purpose |
|---|---|
| `attributevalue/control/{asset_id}` | Immediate ON/OFF control |
| `attributevalue/countdown/{asset_id}` | Schedule a delayed state change |
| `attributevalue/last_state/{asset_id}` | Set relay startup memory mode |

### Publications (Outbound)

| Topic Pattern | Purpose |
|---|---|
| `writeattributevalue/data/{asset_id}` | Telemetry payload (power, energy, status) |
| `writeattributevalue/last_state/{asset_id}` | Report saved last-state to OpenRemote |

### Connection Details

| Parameter | Value |
|---|---|
| Broker Host | `109.176.197.144` |
| Port | `8883` (TLS) |
| Client ID | `Smart_Breakers` |
| Protocol | MQTT v3.1.1 |
| TLS | Enabled (cert verification disabled) |
| Keep-alive | 20 s |
| Reconnect | 5–30 s exponential backoff |

---

## Configuration

Device configurations are defined in [devices.py](devices.py):

| Parameter | Breaker 1 | Breaker 2 |
|---|---|---|
| Protocol Version | 3.5 | 3.4 |
| Switch DPS | `1` | `16` |
| Relay Status DPS | `38` | — |
| Status | ✅ Active | ⛔ Disabled (flag) |

To **enable/disable Breaker 2**, set the flag in `Tuya.py`:

```python
BREAKER2_DISABLED = True   # Set to False to enable Breaker 2
```

When disabled, Breaker 2's device connection, MQTT subscriptions, and telemetry thread are all skipped.

---

## Scheduling & Countdowns

Countdowns are triggered via MQTT and work identically for both breakers:

1. **OpenRemote publishes** a countdown message:
   ```json
   { "state": "OFF", "countdown": "30 minutes" }
   ```
2. **`mqtt_handler.py`** parses the message and appends a job to the in-memory list with an ISO 8601 `execute_at` timestamp.
3. **`scheduler.py`** checks jobs every second. When a job is due, it:
   - Calls `device.set_status()` on the target breaker
   - Syncs internal state memory
   - Forces an immediate telemetry publish
   - Marks the job as `executed`
4. **Jobs are persisted** to `scheduled_commands.json` so they survive restarts.

---

## State Memory & Recovery

### Breaker 1 — Relay Memory (Hardware)

Breaker 1 supports hardware-level relay memory via DPS 38. OpenRemote can set the startup behavior to:

| Mode | Behavior on Power Restore |
|---|---|
| `ON` | Breaker turns ON |
| `OFF` | Breaker stays OFF |
| `MEMORY` | Breaker restores its pre-outage state |

### Breaker 2 — Software State Recovery

Breaker 2 uses a **software-managed state machine** in `breaker2_state_memory.py`:

```
ONLINE ──(telemetry lost)──► OFFLINE
   ▲                            │
   │                     saves last_state
   │                     publishes to OR
   │                            │
   │                     (DPS returns)
   │                            │
   │                  ┌─── 10s delay ───┐
   │                  ▼                 │
   └──(restore applied)◄───────────────┘
```

**Key behaviors:**
- When telemetry is lost (confirmed after 3 empty DPS reads or 2 timeouts), the current state is saved.
- `last_state` is published to OpenRemote as a one-shot notification.
- When DPS returns, a **10-second delay** is applied before restoring the breaker to its pre-outage state.
- Any manual control command during the delay **cancels** the restore.
- Physical button changes are detected by comparing DPS reads and trigger immediate state sync.

---

## Prerequisites

- **Python 3.8+**
- **Network access** to Tuya breakers on the local LAN
- **MQTT broker access** (OpenRemote instance at the configured IP)

### Python Dependencies

| Package | Purpose |
|---|---|
| `tinytuya` | Local Tuya device communication |
| `paho-mqtt` | MQTT client for OpenRemote |

---

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd Tuya

# Install dependencies
pip install tinytuya paho-mqtt
```

---

## Running the Controller

```bash
python Tuya.py
```

### Expected Startup Output

```
[SYSTEM] Starting Tuya Controller (MAIN)
[INIT] ⛔ Removed Breaker 2 config (DISABLED)
[INIT] Breaker 1 connected
[SYSTEM] Devices initialized
[MQTT] Connecting to broker...
[MQTT] Connected successfully
[MQTT] Subscribed -> Breaker 1
[B1-THREAD] Telemetry thread started
[B1-THREAD] DPS fetch interval: 10s
[B1-THREAD] Publish interval: 10s
[WORKER] Tuya worker thread started
[SYSTEM] Controller running
```

### Shutdown

Press `Ctrl+C` for a graceful shutdown. The MQTT connection is cleanly disconnected.

---

## Operational Notes

| Area | Detail |
|---|---|
| **DPS Fetch Timeout** | Each `device.status()` call is wrapped in a 5-second timeout with configurable retries to prevent thread blocking. |
| **Offline Backoff** | Breaker 1 applies a 5-second backoff between reconnection attempts when offline. |
| **Breaker 2 Reconnect** | After 2 consecutive timeouts, an automatic heartbeat-check + connection-reset is attempted (10 s cooldown). |
| **Control De-duplication** | `mqtt_handler.py` queries live DPS before applying a control command — if the breaker is already in the desired state, the command is silently ignored. |
| **Force Publish** | After any control or scheduled action, `telemetry.force_publish` is set to `True`, ensuring the new state is reported immediately regardless of the publish timer. |
| **Persistence** | Countdown jobs are saved to `scheduled_commands.json` on disk after every execution cycle. |

---

<p align="center">
  <b>Visibility Bots</b> · PowerEdge
</p>
