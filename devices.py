"""
devices.py
==========

Device Ownership & Initialization

Responsibilities:
- Store breaker configurations
- Initialize Tuya devices
- Return ready-to-use device objects
"""

import tinytuya

# =====================================================
# BREAKER CONFIGURATIONS
# =====================================================

BREAKER1_CONFIG = {
    "name": "Breaker 1",
    "device_id": "bf3cd80e83e5d72615ahbgy",
    "device_ip": "192.168.0.127",
    "local_key": "Lmcb:8qAX@_g8O15",
    "asset_id": "25dJWcQCswTP6RqcGE0N9t",
    "protocol_version": 3.5,
    "switch_dps": 1,
    "relay_status_dps": 38
}
# pip install -r requirements.txt
BREAKER2_CONFIG = {
    "name": "Breaker 2",
    "device_id": "bf13be77c07479e0da0dr9",
    "device_ip": "192.168.88.110",
    "local_key": "0k<:/1.S8SGsc`2+",
    "asset_id": "2m51jxh1V8tSJlds5py4Wk",
    "protocol_version": 3.4,
    "switch_dps": 16
}


# =====================================================
# DEVICE INITIALIZATION
# =====================================================

def _init_device(cfg):
    """
    Initialize a single Tuya device from config.
    """
    try:
        dev = tinytuya.OutletDevice(
            cfg["device_id"],
            cfg["device_ip"],
            cfg["local_key"]
        )
        dev.set_version(cfg["protocol_version"])
        dev.status()  # handshake / validation
        print(f"[INIT] {cfg['name']} connected")
        return dev
    except Exception as e:
        print(f"[INIT][ERROR] Failed to init {cfg['name']}: {e}")
        return None


# =====================================================
# PUBLIC FACTORIES (USED BY MAIN)
# =====================================================

def get_devices(config_map=None):
    """
    Initialize Tuya devices from config.
    If config_map is provided, only connect to those devices.
    """
    if config_map is None:
        config_map = get_device_configs()
    
    devices = {}
    for asset_id, cfg in config_map.items():
        device = _init_device(cfg)
        if device:
            devices[asset_id] = device
    
    return devices


def get_device_configs():
    """
    Returns a dict of breaker configs keyed by asset_id.
    """
    return {
        BREAKER1_CONFIG["asset_id"]: BREAKER1_CONFIG,
        BREAKER2_CONFIG["asset_id"]: BREAKER2_CONFIG
    }
