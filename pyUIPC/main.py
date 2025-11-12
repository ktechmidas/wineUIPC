# XPPython3 – In-Plugin IPC Bridge (Phase 2)
# -------------------------------------------------------
# Zweck: Opaque Forwarder für WM_COPYDATA→TCP JSON aus der
# uipc_bridge.exe. Kein FSUIPC-Parsen hier, nur Durchreichen
# (Echo-Reply), damit die Roundtrip-Kette steht.
#
# Protokoll (eine JSON-Zeile pro Request/Reply):
#   Request  → {"cmd":"ipc","dwData":<uint32>,"cbData":<int>,"hex":"AABB..."}
#   Response ← {"ok":true,"replyHex":"...","replyDwData":<uint32 optional>}
#               oder {"ok":false,"error":"..."}
#
# Später kann handle_ipc() auf echte FSUIPC-Offsets/Logik gemappt werden
# und X‑Plane DataRefs/Commands im Mainthread bedienen.

import os
import json
import socket
import threading
import struct
import errno
import time
from queue import Queue, Empty
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Callable, List

import xp  # bereitgestellt durch XPPython3

LOG_LEVEL = 2
try:
    with open("pyUIPC.cfg", "r") as cfg:
        for line in cfg:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            if key.strip().lower() == "log_level":
                LOG_LEVEL = int(value.strip())
except Exception:
    LOG_LEVEL = 2

def log_debug(message: str) -> None:
    if LOG_LEVEL >= 2:
        with open("pyUIPC.log", "a") as log_file:
            log_file.write(f"{message}\n")

def log_verbose(message: str) -> None:
    if LOG_LEVEL >= 1:
        with open("pyUIPC.log", "a") as log_file:
            log_file.write(f"{message}\n")

# ---------- Plugin Meta ----------
PLUGIN_NAME = "XPC In-Plugin IPC"
PLUGIN_SIG  = "de.nicorad.xpc.ipc"
PLUGIN_DESC = "Opaque IPC bridge for uipc_bridge.exe (Phase 2)"

# ---------- Config ----------
HOST = os.environ.get("XPC_HOST", "127.0.0.1")
PORT = int(os.environ.get("XPC_PORT", "9000"))
FLIGHTLOOP_INTERVAL = 0.01  # 10 ms – genug für zügige Antworten
MAX_PER_TICK = 100          # Sicherheitslimit
REPLY_TIMEOUT = 2.0         # Sekunden; Netz-Handler wartet so lange auf das Ergebnis

# ---------- Logging ----------

def log(msg: str) -> None:
    xp.log(f"[xpc_ipc] {msg}")

# ---------- Utils ----------

def hex_to_bytes(h: str) -> bytes:
    h = h.strip()
    if len(h) % 2 != 0:
        raise ValueError("hex length must be even")
    return bytes.fromhex(h)

def bytes_to_hex(b: bytes) -> str:
    return b.hex().upper()

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))

# ---------- Request Pipeline (Mainthread Executor) ----------

@dataclass
class Request:
    payload: Dict[str, Any]
    event: threading.Event
    result: Optional[Dict[str, Any]] = None

REQ_QUEUE: "Queue[Request]" = Queue()

# ---------- FSUIPC Memory Model ----------

# Wir halten nur einen minimalen Offset-Satz, genug für APL2:
# 0x3304/3308/330A/333C/3364 – Handshake
# 0x0560/0568/0570/0578/057C/0580 – Position/Attitude
# 0x02B4/02B8/02BC/02C4/02C8 – Geschwindigkeiten
# 0x0366/036C/036D – on ground + Stall/Overspeed
# 0x0020/0B4C – Bodenelevation

MEM_SIZE = 0x8000
mem = bytearray(MEM_SIZE)

# helper to write into mem with bounds checking
def _write(offset: int, data: bytes) -> None:
    end = offset + len(data)
    if offset < 0 or end > len(mem):
        raise ValueError(f"mem write out of range: 0x{offset:04X}")
    mem[offset:end] = data

def _write_int(offset: int, value: int, size: int, signed: bool = False) -> None:
    try:
        b = int(value).to_bytes(size, "little", signed=signed)
    except OverflowError:
        max_val = (2 ** (size * 8 - 1) - 1) if signed else (2 ** (size * 8) - 1)
        min_val = -(2 ** (size * 8 - 1)) if signed else 0
        clamped = max(min(int(value), max_val), min_val)
        b = clamped.to_bytes(size, "little", signed=signed)
    _write(offset, b)

def _write_u8(offset: int, value: int) -> None:
    _write_int(offset, value, 1, signed=False)

def _write_s32(offset: int, value: int) -> None:
    _write_int(offset, value, 4, signed=True)

def _write_u16(offset: int, value: int) -> None:
    _write_int(offset, value, 2, signed=False)

def _write_s16(offset: int, value: int) -> None:
    _write_int(offset, value, 2, signed=True)

def _write_u32(offset: int, value: int) -> None:
    _write_int(offset, value, 4, signed=False)

def _write_s64(offset: int, value: int) -> None:
    _write_int(offset, value, 8, signed=True)

def _write_f64(offset: int, value: float) -> None:
    _write(offset, struct.pack("<d", float(value)))

# --- DataRef bindings (lazy) ---

DATAREFS = {}

def dr(name: str) -> int:
    if name not in DATAREFS:
        DATAREFS[name] = xp.findDataRef(name)
    return DATAREFS[name]

def read_float(name: str) -> float:
    handle = dr(name)
    if handle is None:
        return 0.0
    return xp.getDataf(handle)

def read_double(name: str) -> float:
    handle = dr(name)
    if handle is None:
        return 0.0
    return xp.getDatad(handle)

def read_int(name: str) -> int:
    handle = dr(name)
    if handle is None:
        return 0
    return xp.getDatai(handle)

def read_int_fallback(names: Tuple[str, ...], default: int = 0) -> int:
    for name in names:
        handle = dr(name)
        if handle is not None:
            return xp.getDatai(handle)
    return default

def read_array(name: str, count: int) -> List[float]:
    handle = dr(name)
    if handle is None:
        return [0.0] * count
    buf = [0.0] * count
    xp.getDatavf(handle, buf, 0, count)
    return buf

def read_int_array(name: str, count: int) -> List[int]:
    handle = dr(name)
    if handle is None:
        return [0] * count
    buf = [0] * count
    xp.getDatavi(handle, buf, 0, count)
    return buf

def read_array_range(name: str, start: int, count: int) -> List[float]:
    handle = dr(name)
    if handle is None:
        return [0.0] * count
    buf = [0.0] * count
    xp.getDatavf(handle, buf, start, count)
    return buf

def encode_angle32(deg: float) -> int:
    return int(deg * (65536.0 * 65536.0) / 360.0) & 0xFFFFFFFF

def encode_signed_angle32(deg: float) -> int:
    raw = encode_angle32(deg)
    if raw >= 0x80000000:
        raw -= 0x100000000
    return raw

def encode_heading32(deg: float) -> int:
    return encode_angle32(deg % 360.0)

def encode_altitude_m(meters: float) -> int:
    scale = 65536.0 * 65536.0
    return int(meters * scale)

def encode_speed_knots128(knots: float) -> int:
    return int(knots * 128.0) & 0xFFFFFFFF

def encode_vs_mps256(mps: float) -> int:
    return int(mps * 256.0) & 0xFFFFFFFF

def encode_direction16(deg: float) -> int:
    return int((deg % 360.0) / 360.0 * 65536.0) & 0xFFFF

def encode_bcd4(value: int, *, octal: bool = False) -> int:
    clamped = max(0, min(int(value), 9999))
    digits = [int(x) for x in f"{clamped:04d}"]
    if octal:
        digits = [min(d, 7) for d in digits]
    return (digits[0] << 12) | (digits[1] << 8) | (digits[2] << 4) | digits[3]

LL_SCALE = 10001750.0 * 65536.0 * 65536.0
LON_SCALE = (65536.0 * 65536.0 * 65536.0 * 65536.0) / 360.0

def encode_latitude(deg: float) -> int:
    deg = clamp(deg, -90.0, 90.0)
    return int((deg / 90.0) * LL_SCALE)

def encode_longitude(deg: float) -> int:
    while deg < -180.0:
        deg += 360.0
    while deg > 180.0:
        deg -= 360.0
    return int(deg * LON_SCALE)

def metres_to_fs_ground_alt(metres: float) -> Tuple[int, int]:
    coarse = int(metres * 256.0)
    fine = int(round(metres))
    coarse = max(min(coarse, 0x7FFFFFFF), -0x80000000)
    fine = max(min(fine, 32767), -32768)
    return coarse, fine


def update_snapshot() -> None:
    global _prev_xpdr_code, _prev_xpdr_mode, _last_on_ground, _landing_rate_raw, _landing_rate_frozen
    # Handshake Offsets
    _write_u32(0x3304, 0x19980005)
    _write_u16(0x3308, 10)
    _write_u16(0x330A, 0xFADE)
    _write_u16(0x333C, 1 << 1)
    mem[0x3364] = 0

    lat = read_double("sim/flightmodel/position/latitude")
    lon = read_double("sim/flightmodel/position/longitude")
    alt_m = read_double("sim/flightmodel/position/elevation")
    pitch = read_float("sim/flightmodel/position/theta")
    roll = read_float("sim/flightmodel/position/phi")
    heading_mag = read_float("sim/cockpit/autopilot/heading_mag")
    if heading_mag == 0.0:
        heading_mag = read_float("sim/flightmodel/position/psi")
    gs_mps = read_float("sim/flightmodel/position/groundspeed")
    tas_mps = read_float("sim/flightmodel/position/true_airspeed")
    ias_kts = read_float("sim/cockpit2/gauges/indicators/airspeed_kts_pilot")
    if ias_kts <= 0.0:
        ias_mps_fallback = read_float("sim/flightmodel/position/indicated_airspeed")
        ias_kts = max(0.0, ias_mps_fallback * 1.943844)
    vs_fpm = read_float("sim/flightmodel/position/vh_ind_fpm")
    vs_mps = vs_fpm * 0.00508
    on_ground_any = read_int("sim/flightmodel2/gear/on_ground")
    on_ground_main = read_int("sim/flightmodel/parts/on_ground_main")
    on_ground = 1 if (on_ground_any or on_ground_main) else 0
    log_debug(f"GROUND: any={on_ground_any} main={on_ground_main} -> {on_ground}")
    y_agl = read_float("sim/flightmodel/position/y_agl")

    enc_lat = encode_latitude(lat)
    enc_lon = encode_longitude(lon)
    log(f"LAT encode: raw={lat:.6f} enc={enc_lat}")
    log(f"LON encode: raw={lon:.6f} enc={enc_lon}")
    _write_s64(0x0560, enc_lat)
    _write_s64(0x0568, enc_lon)
    _write_s64(0x0570, encode_altitude_m(alt_m))
    _write_s32(0x0578, encode_signed_angle32(-pitch))  # FS: + = nose down
    _write_s32(0x057C, encode_signed_angle32(-roll))   # FS: + = bank left
    _write_u32(0x0580, encode_angle32(heading_mag % 360.0))
    mag_var = read_float("sim/flightmodel/position/magnetic_variation")
    _write_s16(0x02A0, int(mag_var / 360.0 * 65536.0))

    _write_u32(0x02B4, int(gs_mps * 65536.0))
    _write_u32(0x02B8, encode_speed_knots128(tas_mps * 1.943844))
    _write_u32(0x02BC, encode_speed_knots128(ias_kts))
    _write_u32(0x02C4, encode_speed_knots128(320.0))
    _write_u32(0x02C8, encode_vs_mps256(vs_mps))

    if on_ground == 0:
        _landing_rate_frozen = False
        _landing_rate_raw = int(vs_mps * 256.0)
    elif not _landing_rate_frozen and y_agl < 2.0:
        _landing_rate_raw = int(vs_mps * 256.0)
        _landing_rate_frozen = True
        log(f"Landing rate captured: {_landing_rate_raw / 256.0 * 60 * 3.28084:.2f} fpm")
    _write_s32(0x030C, _landing_rate_raw)
    _write_u8(0x0366, on_ground)
    log_verbose(f"GROUND FLAG set to {on_ground}")
    _last_on_ground = on_ground

    stall_ratio = clamp(read_float("sim/flightmodel2/misc/stall_warning_ratio"), 0.0, 1.0)
    overspeed_ratio = clamp(read_float("sim/flightmodel2/misc/overspeed_warning_ratio"), 0.0, 1.0)
    _write_u8(0x036C, 1 if stall_ratio > 0.05 else 0)
    _write_u8(0x036D, 1 if overspeed_ratio > 0.05 else 0)

    paused = 1 if read_int("sim/time/paused") else 0
    _write_u16(0x0262, paused)
    _write_u16(0x0264, paused)

    ground_alt_m = alt_m - y_agl
    coarse, fine = metres_to_fs_ground_alt(ground_alt_m)
    _write_s32(0x0020, coarse)
    _write_int(0x0B4C, fine, 2, signed=True)

    # Lights
    nav_on = 1 if read_int("sim/cockpit2/switches/navigation_lights_on") else 0
    beacon_on = 1 if read_int("sim/cockpit2/switches/beacon_on") else 0
    strobe_on = 1 if read_int("sim/cockpit2/switches/strobe_lights_on") else 0
    landing_on = 1 if read_int("sim/cockpit2/switches/landing_lights_on") else 0
    taxi_on = 1 if read_int("sim/cockpit2/switches/taxi_light_on") else 0
    panel_ratio = read_float("sim/cockpit2/switches/panel_brightness_ratio_all")
    panel_on = 1 if panel_ratio > 0.1 else 0
    _write_u8(0x0280, nav_on)
    _write_u8(0x0281, 1 if (beacon_on or strobe_on) else 0)
    _write_u8(0x028C, landing_on)
    log_verbose(
        f"LIGHTS nav={nav_on} beacon={beacon_on} strobe={strobe_on} "
        f"landing={landing_on} taxi={taxi_on} panel={panel_on}"
    )

    lights_bits = 0
    lights_bits |= nav_on << 0
    lights_bits |= beacon_on << 1
    lights_bits |= landing_on << 2
    lights_bits |= taxi_on << 3
    lights_bits |= strobe_on << 4
    lights_bits |= panel_on << 5
    lights_bits |= nav_on << 6  # recognition
    lights_bits |= nav_on << 7  # wing
    lights_bits |= nav_on << 8  # logo
    lights_bits |= nav_on << 9  # cabin
    _write_u16(0x0D0C, lights_bits)

    # Parking brake
    brake_ratio = clamp(read_float("sim/flightmodel/controls/parkbrake"), 0.0, 1.0)
    _write_u16(0x0BC8, int(brake_ratio * 32767.0))

    # Flaps / Spoilers
    flap_ratio = clamp(read_float("sim/flightmodel/controls/flaprat"), 0.0, 1.0)
    spoiler_ratio = clamp(read_float("sim/flightmodel/controls/sbrkrat"), 0.0, 1.0)
    flap_units = int(flap_ratio * 16383.0)
    spoiler_units = int(spoiler_ratio * 16383.0)
    _write_u32(0x0BDC, flap_units)
    _write_u32(0x0BE0, flap_units)
    _write_u32(0x0BE4, flap_units)
    _write_u32(0x0BD0, spoiler_units)
    _write_u32(0x0BD4, spoiler_units)
    _write_u32(0x0BD8, spoiler_units)
    spoiler_arm = 1 if read_int("sim/cockpit2/switches/speedbrake_arm") else 0
    _write_u32(0x0BCC, 4800 if spoiler_arm else 0)

    # Gear
    gear_handle = read_int("sim/cockpit2/controls/gear_handle_down")
    _write_u16(0x0BE8, 1 if gear_handle else 0)
    log_verbose(f"GEAR HANDLE: {gear_handle}")
    gear_type = read_float("sim/flightmodel/misc/gear_type")
    if gear_type <= 0.5:
        gear_flags = 0
    elif gear_type < 2.0:
        gear_flags = 0
    elif gear_type < 4.0:
        gear_flags = 1
    else:
        gear_flags = 2
    _write_u16(0x060C, gear_flags)
    _write_u16(0x060E, 1 if gear_flags == 1 else 0)
    log_verbose(f"GEAR TYPE: xp={gear_type:.1f} fsuipc={gear_flags}")
    deploy = read_array("sim/flightmodel/parts/gear_deploy", 3)
    deploy_offsets = (0x0C34, 0x0C30, 0x0C38)
    all_down = True
    for idx, off in enumerate(deploy_offsets):
        ratio = clamp(deploy[idx] if idx < len(deploy) else 0.0, 0.0, 1.0)
        if ratio < 0.99:
            all_down = False
        _write_u16(off, int(ratio * 16383.0))
    _write_u16(0x0C3C, 16383 if all_down else 0)
    log_verbose(f"GEAR DEPLOY: mainL={deploy[0]:.2f} mainR={deploy[1]:.2f} nose={deploy[2]:.2f} all_down={all_down}")

    # Engines
    n1 = read_array("sim/flightmodel/engine/ENGN_N1_", 4)
    n2 = read_array("sim/flightmodel/engine/ENGN_N2_", 4)
    eng_running = read_int_array("sim/flightmodel/engine/ENGN_running", 4)
    fuel_flow_gph = read_array("sim/flightmodel/misc/fuel_flow_gph", 4)
    oil_temp = read_array("sim/flightmodel/engine/ENGN_oilt", 4)
    oil_press = read_array("sim/flightmodel/engine/oil_pressure_psi", 4)
    engine_slots = (
        (0x0894, 0x0896, 0x0898, 0x090A, 0x08B8, 0x08BA),
        (0x092C, 0x092E, 0x0930, 0x0942, 0x0950, 0x0952),
        (0x09C4, 0x09C6, 0x09C8, 0x09DA, 0x09E8, 0x09EA),
        (0x0A5C, 0x0A5E, 0x0A60, 0x0A72, 0x0A80, 0x0A82),
    )
    for idx, (comb_off, n2_off, n1_off, ff_off, oil_temp_off, oil_press_off) in enumerate(engine_slots):
        _write_u16(n2_off, 0xFFFF)
        _write_u16(n1_off, 0xFFFF)
        n1_val = n1[idx] if idx < len(n1) else 0.0
        n2_val = n2[idx] if idx < len(n2) else 0.0
        ff_gph = fuel_flow_gph[idx] if idx < len(fuel_flow_gph) else 0.0
        temp_c = oil_temp[idx] if idx < len(oil_temp) else 0.0
        press_psi = oil_press[idx] if idx < len(oil_press) else 0.0
        running = eng_running[idx] if idx < len(eng_running) else 0
        if running:
            _write_u16(n2_off, int(clamp(n2_val, 0.0, 110.0) / 100.0 * 16384.0))
            _write_u16(n1_off, int(clamp(n1_val, 0.0, 110.0) / 100.0 * 16384.0))
        combust = 1 if running else 0
        _write_u16(comb_off, combust)
        lbs_per_hr = clamp(ff_gph * 6.7, 0.0, 65535.0)
        _write_u32(ff_off, int(lbs_per_hr))
        _write_u16(oil_temp_off, int(clamp(temp_c * 9.0 / 5.0 + 32.0, -273.0, 999.0) / 140.0 * 16384.0))
        _write_u16(oil_press_off, int(clamp(press_psi, 0.0, 220.0) / 55.0 * 16384.0))
    engine_count = read_int("sim/aircraft/prop/acf_num_engines")
    if engine_count <= 0:
        engine_count = len(n1) if n1 else 1
    engine_count = max(1, min(engine_count, len(engine_slots)))
    _write_u16(0x0AEC, engine_count)

    # Fuel (approximate)
    fuel_total = read_float("sim/flightmodel/weight/m_fuel_total")
    fuel_pct = clamp(fuel_total / 3000.0, 0.0, 1.0)
    units = int(fuel_pct * 128.0 * 65536.0)
    _write_u32(0x0B7C, units)
    _write_u32(0x0B94, units)

    # Cabin signs (best effort)
    seatbelt_mode = clamp(read_int("sim/cockpit2/switches/fasten_seat_belts"), 0, 2)
    nosmoke_mode = clamp(read_int("sim/cockpit2/switches/no_smoking"), 0, 2)
    _write_u8(0x3414, int(seatbelt_mode))
    _write_u8(0x3415, int(nosmoke_mode))

    xpdr_code = clamp(read_int_fallback((
        "sim/cockpit2/radios/actuators/transponder_code",
        "sim/cockpit/radios/transponder_code",
    )), 0, 7777)
    encoded_code = encode_bcd4(int(xpdr_code), octal=True)
    _write_u16(0x0354, encoded_code)
    xpdr_mode = clamp(read_int_fallback((
        "sim/cockpit2/radios/actuators/transponder_mode",
        "sim/cockpit/radios/transponder_mode",
    )), 0, 4)
    if xpdr_mode <= 0:
        fs_xpdr_mode = 0  # OFF/GND
    elif xpdr_mode == 1:
        fs_xpdr_mode = 1  # STBY
    elif xpdr_mode == 3:
        fs_xpdr_mode = 2  # TEST
    elif xpdr_mode == 4:
        fs_xpdr_mode = 3  # ON (Mode A)
    else:
        fs_xpdr_mode = 4  # ALT (Mode C/S)
    _write_u8(0x0B46, fs_xpdr_mode)
    _write_u8(0x7B91, fs_xpdr_mode)
    if _prev_xpdr_code != encoded_code or _prev_xpdr_mode != fs_xpdr_mode:
        log(f"XPDR code={xpdr_code:04d} encoded=0x{encoded_code:04X} mode={fs_xpdr_mode}")
        _prev_xpdr_code = encoded_code
        _prev_xpdr_mode = fs_xpdr_mode

    # G-force (normal)
    g_force = clamp(read_float("sim/flightmodel2/misc/gforce_normal"), -8.0, 8.0)
    g_units = int(g_force * 625.0)
    _write_int(0x11BA, g_units, 2, signed=True)
    _write_int(0x11B8, g_units, 2, signed=True)

    # Wind (surface + ambient simple mapping: use first layer)
    wind_speeds = read_array_range("sim/weather/wind_speed_kt", 0, 3)
    wind_dirs = read_array_range("sim/weather/wind_direction_degt", 0, 3)
    wind_speed = clamp(wind_speeds[0] if wind_speeds else 0.0, 0.0, 65535.0)
    wind_dir = wind_dirs[0] if wind_dirs else 0.0
    wind_speed_u16 = int(wind_speed + 0.5)
    wind_dir_u16 = encode_direction16(wind_dir)
    _write_u16(0x0E90, wind_speed_u16)
    _write_u16(0x0E92, wind_dir_u16)
    _write_u16(0x0EF0, wind_speed_u16)
    _write_u16(0x0EF2, wind_dir_u16)

# parse FS6IPC block

def parse_ipc_block(data: bytearray) -> bytearray:
    update_snapshot()
    pos = 0
    end = len(data)
    log(f"parse_ipc_block size={end}")
    while pos + 4 <= end:
        cmd = int.from_bytes(data[pos:pos+4], "little")
        next_bytes = bytes_to_hex(data[pos:pos+16])
        log(f"  block cmd=0x{cmd:08X} pos=0x{pos:04X} next={next_bytes}")
        if cmd == 0:
            break
        if cmd == FS6IPC_READSTATEDATA_ID:
            if pos + 16 > end:
                raise ValueError("READ header truncated")
            dwOffset = int.from_bytes(data[pos+4:pos+8], "little")
            nBytes = int.from_bytes(data[pos+8:pos+12], "little")
            payload = pos + 16
            if payload + nBytes > end:
                raise ValueError("READ payload truncated")
            data[payload:payload+nBytes] = mem[dwOffset:dwOffset+nBytes]
            pos = payload + nBytes
        elif cmd == FS6IPC_WRITESTATEDATA_ID:
            if pos + 12 > end:
                raise ValueError("WRITE header truncated")
            dwOffset = int.from_bytes(data[pos+4:pos+8], "little")
            nBytes = int.from_bytes(data[pos+8:pos+12], "little")
            payload = pos + 12
            if payload + nBytes > end:
                raise ValueError("WRITE payload truncated")
            # TODO: optional -> DataRefs setzen
            pos = payload + nBytes
        else:
            log(f"unknown block id 0x{cmd:04X} at pos=0x{pos:04X}, ignoring remainder")
            break
    return data

FS6IPC_READSTATEDATA_ID = 1
FS6IPC_WRITESTATEDATA_ID = 2

# ---------- Core IPC Handler (runs on main thread) ----------

def handle_ipc(dwData: int, payload: bytes) -> Dict[str, Any]:
    block = bytearray(payload)
    try:
        reply = parse_ipc_block(block)
    except Exception as exc:
        log(f"parse error: {exc}")
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "replyHex": bytes_to_hex(reply), "replyDwData": int(dwData)}

# ---------- FlightLoop (Mainthread executor) ----------

def _flightloop_cb(elapsedSinceLastCall, elapsedTimeSinceLastFlightLoop, counter, refcon):
    handled = 0
    while handled < MAX_PER_TICK:
        try:
            req: Request = REQ_QUEUE.get_nowait()
        except Empty:
            break
        try:
            p = req.payload
            cmd = str(p.get("cmd", "")).strip().lower()
            log(f"dispatch cmd={cmd}")
            if cmd == "ipc":
                dw = int(p.get("dwData", 0))
                cb = int(p.get("cbData", 0))
                hexstr = str(p.get("hex", ""))
                data = hex_to_bytes(hexstr)
                if cb and cb != len(data):
                    # Warnung, aber wir nehmen die tatsächliche Länge
                    log(f"cbData mismatch: cb={cb} len(hex)={len(data)} – using len(hex)")
                req.result = handle_ipc(dw, data)
            else:
                req.result = {"ok": False, "error": f"unknown cmd: {cmd}"}
        except Exception as e:
            req.result = {"ok": False, "error": str(e)}
        finally:
            req.event.set()
            handled += 1
    return FLIGHTLOOP_INTERVAL

# ---------- TCP Server (Background thread) ----------

def _serve():
    global _server_socket
    log(f"TCP server on {HOST}:{PORT}")

    bind_delay = 0.1
    s: Optional[socket.socket] = None
    while not _server_stop.is_set():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, PORT))
            s.listen(5)
            s.settimeout(1.0)
            break
        except OSError as exc:
            s.close()
            s = None
            if exc.errno == errno.EADDRINUSE:
                log(f"port {PORT} busy, retrying in {bind_delay:.2f}s")
                time.sleep(bind_delay)
                bind_delay = min(bind_delay * 2.0, 1.0)
                continue
            raise

    if s is None:
        log("server exit before bind (stop requested)")
        return

    _server_socket = s
    try:
        while not _server_stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                if _server_stop.is_set():
                    break
                raise
            threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()
    finally:
        try:
            s.close()
        finally:
            _server_socket = None


def _send_line(conn: socket.socket, obj: Dict[str, Any]) -> None:
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    conn.sendall(line.encode("utf-8"))


def _handle_client(conn: socket.socket, addr):
    conn.settimeout(60)
    with conn:
        buf = b""
        log(f"client {addr} connected")
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    _process_line(conn, line)
        except socket.timeout:
            return
        except Exception as e:
            try:
                _send_line(conn, {"ok": False, "error": str(e)})
            except Exception:
                pass
            return
        finally:
            log(f"client {addr} disconnected")


def _process_line(conn: socket.socket, line: bytes) -> None:
    try:
        payload = json.loads(line.decode("utf-8"))
    except Exception as e:
        _send_line(conn, {"ok": False, "error": f"invalid json: {e}"})
        return

    log(f"recv payload keys={list(payload.keys())}")

    ev = threading.Event()
    req = Request(payload=payload, event=ev)
    REQ_QUEUE.put(req)

    if not ev.wait(timeout=REPLY_TIMEOUT):
        _send_line(conn, {"ok": False, "error": "timeout"})
        return

    _send_line(conn, req.result or {"ok": False, "error": "no result"})

# ---------- Plugin Lifecycle ----------
_server_thread: Optional[threading.Thread] = None
_server_socket: Optional[socket.socket] = None
_server_stop = threading.Event()
_flightloop = None
_prev_xpdr_code: Optional[int] = None
_prev_xpdr_mode: Optional[int] = None
_last_on_ground = 0
_landing_rate_raw = 0
_landing_rate_frozen = False


def XPluginStart():
    log(f"start module={__file__}")
    global _flightloop
    _flightloop = xp.createFlightLoop(_flightloop_cb)
    xp.scheduleFlightLoop(_flightloop, FLIGHTLOOP_INTERVAL, True)
    return PLUGIN_NAME, PLUGIN_SIG, PLUGIN_DESC


def XPluginStop():
    log("stop")


def XPluginEnable():
    log("enable")
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        log("server thread already running, skipping restart")
    else:
        _server_stop.clear()
        _server_thread = threading.Thread(target=_serve, daemon=True)
        _server_thread.start()
    return 1


def XPluginDisable():
    log("disable")
    global _server_socket, _server_thread
    _server_stop.set()
    if _server_socket:
        try:
            _server_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            try:
                _server_socket.close()
            except OSError:
                pass
            _server_socket = None
    if _server_thread:
        _server_thread.join(timeout=1.0)
        if _server_thread.is_alive():
            log("server thread did not exit cleanly")
        _server_thread = None
