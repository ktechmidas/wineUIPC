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
import traceback
from queue import Queue, Empty
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Callable, List

import xp  # bereitgestellt durch XPPython3

PLUGIN_DIR = os.path.dirname(__file__)
CFG_PATH = os.path.join(PLUGIN_DIR, "wineUIPC.cfg")
LOG_PATH = os.path.join(PLUGIN_DIR, "wineUIPC.log")

CFG_DEFAULTS = {
    "log_level": "2",
    "host": "127.0.0.1",
    "port": "9000",
    "fs_version": "14",            # MSFS 2024 code
    "fsuipc_version": "7.505",     # FSUIPC 7.505 (BCD 0x7505)
    "fsuipc_build_letter": "",     # optional (a-z)
}
_LOG_LOCK = threading.Lock()


def _write_cfg(cfg: Dict[str, str]) -> None:
    lines = [f"{k}={v}\n" for k, v in cfg.items()]
    with open(CFG_PATH, "w") as f:
        f.writelines(lines)


def _load_cfg() -> Dict[str, str]:
    cfg = dict(CFG_DEFAULTS)
    try:
        with open(CFG_PATH, "r") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                key = key.strip().lower()
                value = value.strip()
                if key:
                    cfg[key] = value
    except FileNotFoundError:
        _write_cfg(cfg)
    except Exception:
        pass
    return cfg


_CFG = _load_cfg()
LOG_LEVEL = int(_CFG.get("log_level", CFG_DEFAULTS["log_level"]))
_CFG_HOST = _CFG.get("host", CFG_DEFAULTS["host"])
_CFG_PORT = _CFG.get("port", CFG_DEFAULTS["port"])


def _parse_fsuipc_version_x1000(value: str, default_hex: int) -> int:
    """
    Convert version string (e.g. "7.505" or "0x7505") to the BCD int used in 0x3304 HIWORD.
    """
    if not value:
        return default_hex
    s = value.strip()
    if not s:
        return default_hex
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
    except Exception:
        pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return default_hex
    digits = digits[:4]
    try:
        return int(digits, 16)
    except Exception:
        return default_hex


def _parse_build_letter(value: str, default_val: int) -> int:
    """
    Convert build letter (a-z) or integer to the numeric code expected by offset 0x3304 LOWORD.
    a=1, b=2, ..., z=26; 0 means none.
    """
    if not value:
        return default_val
    s = value.strip()
    if not s:
        return default_val
    if len(s) == 1 and s.isalpha():
        n = ord(s.lower()) - 96
        if 0 <= n <= 26:
            return n
    try:
        n_int = int(s)
        return max(0, min(26, n_int))
    except Exception:
        return default_val


_DEFAULT_FS_VERSION = int(CFG_DEFAULTS["fs_version"])
_DEFAULT_FSUIPC_X1000 = _parse_fsuipc_version_x1000(CFG_DEFAULTS["fsuipc_version"], 0x7505)
_DEFAULT_BUILD_LETTER = _parse_build_letter(CFG_DEFAULTS["fsuipc_build_letter"], 0)

def _write_log(level: str, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{level}] {message}\n"
    with _LOG_LOCK:
        with open(LOG_PATH, "a") as log_file:
            log_file.write(line)

def log_debug(message: str) -> None:
    if LOG_LEVEL >= 2:
        _write_log("DEBUG", message)

def log_verbose(message: str) -> None:
    if LOG_LEVEL >= 1:
        _write_log("VERBOSE", message)

# ---------- Plugin Meta ----------
PLUGIN_NAME = "XPC In-Plugin IPC"
PLUGIN_SIG  = "de.nicorad.xpc.ipc"
PLUGIN_DESC = "Opaque IPC bridge for uipc_bridge.exe (Phase 2)"

# ---------- Config ----------
HOST = os.environ.get("XPC_HOST", _CFG.get("host", CFG_DEFAULTS["host"]))
PORT_STR = os.environ.get("XPC_PORT", _CFG.get("port", CFG_DEFAULTS["port"]))
try:
    PORT = int(PORT_STR)
except ValueError:
    PORT = int(CFG_DEFAULTS["port"])

HANDSHAKE_FS_VERSION = _DEFAULT_FS_VERSION
try:
    HANDSHAKE_FS_VERSION = int(os.environ.get("XPC_FS_VERSION", _CFG.get("fs_version", CFG_DEFAULTS["fs_version"])))
except Exception:
    HANDSHAKE_FS_VERSION = _DEFAULT_FS_VERSION

_HANDSHAKE_FSUIPC_VERSION_STR = os.environ.get(
    "XPC_FSUIPC_VERSION",
    _CFG.get("fsuipc_version", CFG_DEFAULTS["fsuipc_version"]),
)
HANDSHAKE_FSUIPC_VER_X1000 = _parse_fsuipc_version_x1000(
    _HANDSHAKE_FSUIPC_VERSION_STR,
    _DEFAULT_FSUIPC_X1000,
)
_HANDSHAKE_BUILD_LETTER_STR = os.environ.get(
    "XPC_FSUIPC_BUILD",
    _CFG.get("fsuipc_build_letter", CFG_DEFAULTS["fsuipc_build_letter"]),
)
HANDSHAKE_BUILD_LETTER = _parse_build_letter(
    _HANDSHAKE_BUILD_LETTER_STR,
    _DEFAULT_BUILD_LETTER,
)

_CFG.update({
    "host": HOST,
    "port": str(PORT),
    "log_level": str(LOG_LEVEL),
    "fs_version": str(HANDSHAKE_FS_VERSION),
    "fsuipc_version": str(_HANDSHAKE_FSUIPC_VERSION_STR or CFG_DEFAULTS["fsuipc_version"]),
    "fsuipc_build_letter": str(_HANDSHAKE_BUILD_LETTER_STR or CFG_DEFAULTS["fsuipc_build_letter"]),
})
try:
    _write_cfg(_CFG)
except Exception:
    pass
FLIGHTLOOP_INTERVAL = 0.01  # 10 ms – genug für zügige Antworten
MAX_PER_TICK = 100          # Sicherheitslimit
REPLY_TIMEOUT = 5.0         # Sekunden; Netz-Handler wartet so lange auf das Ergebnis
MAX_SPOILER_DEFLECTION_DEG = 60.0  # reasonable default for scaling
FUEL_LBS_PER_GAL = 6.7
KG_TO_LBS = 2.20462262185
CABIN_SIGN_SOURCES = {
    "seatbelt": (
        ("laminar/B738/toggle_seatbelt_sign", "bool"),
        ("AirbusFBW/SeatBeltSignsOn", "bool"),
        ("XCrafts/ERJ/overhead/seat_belts", "mode"),
        ("ff/seatsigns_on", "bool"),
        ("sim/cockpit2/annunciators/seatbelt_on", "bool"),
        ("sim/cockpit2/switches/fasten_seat_belts", "mode"),
    ),
    "nosmoke": (
        ("laminar/B738/toggle_smoking_sign", "bool"),
        ("AirbusFBW/NoSmokingSignsOn", "bool"),
        ("XCrafts/ERJ/overhead/no_smoking", "mode"),
        ("sim/cockpit2/annunciators/smoking_on", "bool"),
        ("sim/cockpit2/switches/no_smoking", "mode"),
    ),
}
RADIO_SOURCES = {
    "com1_active": (
        ("sim/cockpit2/radios/actuators/com1_frequency_hz_833", 0.001),
    ),
    "com1_stby": (
        ("sim/cockpit2/radios/actuators/com1_standby_frequency_hz_833", 0.001),
    ),
    "com2_active": (
        ("sim/cockpit2/radios/actuators/com2_frequency_hz_833", 0.001),
    ),
    "com2_stby": (
        ("sim/cockpit2/radios/actuators/com2_standby_frequency_hz_833", 0.001),
    ),
}

# ---------- Logging ----------

def log(msg: str) -> None:
    if LOG_LEVEL >= 1:
        _write_log("INFO", msg)

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

def first(seq: List[float], default: float = 0.0) -> float:
    return seq[0] if seq else default

def _spoiler_deg_to_units(deg: float) -> int:
    ratio = clamp(deg / MAX_SPOILER_DEFLECTION_DEG, 0.0, 1.0)
    return int(ratio * 16383.0)

def _avg_spoiler_deflection(values: List[float], start: int, count: int) -> float:
    acc: List[float] = []
    end = min(start + count, len(values))
    for idx in range(start, end):
        acc.append(max(0.0, values[idx]))
    if not acc:
        return 0.0
    return sum(acc) / len(acc)

def _resolve_cabin_sign(sign: str) -> int:
    sources = CABIN_SIGN_SOURCES.get(sign, ())
    for dataref, mode in sources:
        if mode == "bool":
            val = read_int_optional(dataref)
            if val is None:
                float_val = read_float_optional(dataref)
                if float_val is None:
                    continue
                val = 1 if float_val >= 0.5 else 0
            val = 1 if val else 0
            return 2 if val else 0
        if mode == "mode":
            raw = read_int_optional(dataref)
            if raw is None:
                continue
            return int(clamp(raw, 0, 2))
    return 0

# ---------- Request Pipeline (Mainthread Executor) ----------

@dataclass
class Request:
    payload: Dict[str, Any]
    event: threading.Event
    result: Optional[Dict[str, Any]] = None

REQ_QUEUE: "Queue[Request]" = Queue()
_toast_text: Optional[str] = None
_toast_expires: float = 0.0
_toast_window: Optional[int] = None
_toast_draw_registered = False


def _show_toast(message: str, seconds: float = 4.0) -> None:
    global _toast_text, _toast_expires
    _toast_text = message
    _toast_expires = time.time() + max(1.0, seconds)


def _toast_draw(phase: int, is_before: int, refcon: Any) -> int:
    global _toast_text, _toast_expires
    if not _toast_text:
        return 1
    if time.time() > _toast_expires:
        _toast_text = None
        return 1
    left, top, right, bottom = xp.getScreenBoundsGlobal()
    x = left + 20
    y = top - 40
    xp.drawString((1.0, 1.0, 0.2), x, y, _toast_text, None, xp.Font_Basic)
    return 1

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

def read_int_optional(name: str) -> Optional[int]:
    handle = dr(name)
    if handle is None:
        return None
    return xp.getDatai(handle)

def read_float_optional(name: str) -> Optional[float]:
    handle = dr(name)
    if handle is None:
        return None
    return xp.getDataf(handle)

def read_int_fallback(names: Tuple[str, ...], default: int = 0) -> int:
    for name in names:
        handle = dr(name)
        if handle is not None:
            return xp.getDatai(handle)
    return default

def read_float_fallback(names: Tuple[str, ...], default: float = 0.0) -> float:
    for name in names:
        handle = dr(name)
        if handle is not None:
            return xp.getDataf(handle)
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

def read_string(name: str, max_len: int = 260) -> str:
    handle = dr(name)
    if handle is None:
        return ""
    buf = bytearray(max_len)
    try:
        xp.getDatab(handle, buf, 0, max_len)
    except Exception:
        return ""
    return buf.rstrip(b"\x00").decode("utf-8", errors="ignore")

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

def _read_number_optional(name: str) -> Optional[float]:
    val_int = read_int_optional(name)
    if val_int is not None:
        return float(val_int)
    return read_float_optional(name)

def encode_bcd4(value: int, *, octal: bool = False) -> int:
    clamped = max(0, min(int(value), 9999))
    digits = [int(x) for x in f"{clamped:04d}"]
    if octal:
        digits = [min(d, 7) for d in digits]
    return (digits[0] << 12) | (digits[1] << 8) | (digits[2] << 4) | digits[3]

def encode_com_freq(freq_input: float) -> int:
    if freq_input <= 0.0:
        return 0
    # freq_input expected in MHz (e.g. 123.800)
    freq_mhz = round(freq_input * 40.0) / 40.0  # snap to 0.025 MHz (covers 8.33 too)
    # FSUIPC stores 4 BCD digits with the leading "1" assumed (e.g. 123.80 -> 0x2380)
    bcd_number = int(round(freq_mhz * 100.0))
    # Ensure we have 4 digits for BCD (strip leading 1 and clamp)
    if bcd_number >= 10000:
        bcd_number -= 10000  # strip leading 1 (e.g. 12345 -> 2345)
    bcd_number = max(0, min(bcd_number, 9999))
    return encode_bcd4(bcd_number)

def write_ascii(offset: int, text: str, size: int) -> None:
    data = bytearray(size)
    encoded = (text or "").encode("utf-8", errors="ignore")[:size]
    data[:len(encoded)] = encoded
    _write(offset, bytes(data))

def _read_radio_frequency(key: str) -> float:
    for name, scale in RADIO_SOURCES.get(key, ()):
        val = _read_number_optional(name)
        if val is None:
            continue
        freq = float(val) * scale
        if freq > 0.0:
            return freq
    return 0.0


def _read_radio_frequency_debug(key: str) -> Tuple[float, str]:
    for name, scale in RADIO_SOURCES.get(key, ()):
        val = _read_number_optional(name)
        if val is None:
            continue
        freq = float(val) * scale
        if freq > 0.0:
            return freq, name
    return 0.0, ""

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
    global _prev_xpdr_code, _prev_xpdr_mode, _last_on_ground, _landing_rate_raw, _landing_rate_frozen, _handshake_logged
    # Handshake Offsets
    # HIWORD = FSUIPC version * 1000 (per FSUIPC spec, BCD), LOWORD = build letter (a=1)
    # Values are configurable via wineUIPC.cfg or XPC_FSUIPC_VERSION / XPC_FSUIPC_BUILD / XPC_FS_VERSION env vars.
    version_x1000 = HANDSHAKE_FSUIPC_VER_X1000
    build_letter = HANDSHAKE_BUILD_LETTER
    fs_version = HANDSHAKE_FS_VERSION
    _write_u32(0x3304, (version_x1000 << 16) | build_letter)
    _write_u16(0x3308, fs_version)
    _write_u16(0x330A, 0xFADE)
    _write_u16(0x333C, 1 << 1)
    mem[0x3364] = 0
    if not _handshake_logged:
        log(f"FSUIPC handshake version={version_x1000 >> 12}.{(version_x1000 >> 8) & 0xF}{(version_x1000 >> 4) & 0xF}{version_x1000 & 0xF} build=0x{build_letter:04X} fs_ver={fs_version} raw=0x{(version_x1000 << 16) | build_letter:08X}")
        _handshake_logged = True

    lat = read_double("sim/flightmodel/position/latitude")
    lon = read_double("sim/flightmodel/position/longitude")
    alt_m = read_double("sim/flightmodel/position/elevation")
    indicated_alt_ft = read_float_fallback((
        "sim/cockpit2/gauges/indicators/altitude_ft_pilot",
        "sim/cockpit/altimeter/indicated-altitude",
    ), 0.0)
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
    gear_on_ground = read_int_array("sim/flightmodel2/gear/on_ground", 3)
    on_ground_any = any(gear_on_ground)
    on_ground_main = read_int("sim/flightmodel/parts/on_ground_main")
    on_ground = 1 if (on_ground_any or on_ground_main) else 0
    failure_onground = 1 if read_int("sim/flightmodel/failures/onground_any") else 0
    log_debug(f"GROUND: gear={gear_on_ground} main={on_ground_main} -> {on_ground}")
    y_agl = read_float("sim/flightmodel/position/y_agl")

    enc_lat = encode_latitude(lat)
    enc_lon = encode_longitude(lon)
    log_debug(f"LAT encode: raw={lat:.6f} enc={enc_lat}")
    log_debug(f"LON encode: raw={lon:.6f} enc={enc_lon}")
    _write_s64(0x0560, enc_lat)
    _write_s64(0x0568, enc_lon)
    _write_s64(0x0570, encode_altitude_m(alt_m))
    _write_s32(0x3324, int(indicated_alt_ft))
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
        log_debug(f"Landing rate captured: {_landing_rate_raw / 256.0 * 60 * 3.28084:.2f} fpm")
    _write_s32(0x030C, _landing_rate_raw)
    _write_u8(0x0366, on_ground)
    log_verbose(f"GROUND FLAG set to {on_ground}")
    _last_on_ground = on_ground
    over_g = 1 if read_int("sim/flightmodel/failures/over_g") else 0
    landing_rate_fpm = (_landing_rate_raw / 256.0) * 60.0 * 3.28084
    hard_landing = 1 if (_landing_rate_frozen and landing_rate_fpm <= -2500.0) else 0
    crash_flag = 1 if ((over_g and failure_onground) or hard_landing) else 0
    _write_u16(0x0840, crash_flag)
    if crash_flag:
        log_debug(
            f"CRASH detected over_g={over_g} onground_fail={failure_onground} "
            f"landing_rate_fpm={landing_rate_fpm:.1f}"
        )

    stall_ratio = clamp(read_float("sim/flightmodel2/misc/stall_warning_ratio"), 0.0, 1.0)
    stall_annun = read_int_optional("sim/cockpit2/annunciators/stall_warning")
    stall_flag = 1 if stall_ratio > 0.05 else 0
    if stall_annun is not None and stall_annun > 0:
        stall_flag = 1
    overspeed_ratio = clamp(read_float("sim/flightmodel2/misc/overspeed_warning_ratio"), 0.0, 1.0)
    _write_u8(0x036C, stall_flag)
    overspeed_pref = read_int_optional("sim/operation/prefs/warn_overspeed")
    overspeed_flag = 1 if overspeed_ratio > 0.05 else 0
    if overspeed_pref is not None and overspeed_pref > 0:
        overspeed_flag = 1
    _write_u8(0x036D, overspeed_flag)

    paused = 1 if read_int("sim/time/paused") else 0
    _write_u16(0x0262, paused)
    _write_u16(0x0264, paused)

    ground_alt_m = alt_m - y_agl
    coarse, fine = metres_to_fs_ground_alt(ground_alt_m)
    _write_s32(0x0020, coarse)
    _write_int(0x0B4C, fine, 2, signed=True)
    _write_u32(0x31E4, int(max(0.0, y_agl) * 65536.0 + 0.5))

    sim_rate_actual = read_float_optional("sim/time/sim_speed_actual")
    if sim_rate_actual is None or sim_rate_actual <= 0.0:
        sim_rate_actual = read_float_fallback(("sim/time/sim_speed", "sim/time/sim_rate"), 1.0)
    sim_rate = clamp(sim_rate_actual, 0.1, 64.0)
    _write_u16(0x0C1A, int(sim_rate * 256.0 + 0.5))

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
    log_debug(
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
    spoiler_deflections = read_array("sim/flightmodel2/controls/spoiler_deflection_deg", 20)
    left_def = _avg_spoiler_deflection(spoiler_deflections, 0, 10)
    right_def = _avg_spoiler_deflection(spoiler_deflections, 10, 10)
    _write_u32(0x0BD4, _spoiler_deg_to_units(left_def))
    _write_u32(0x0BD8, _spoiler_deg_to_units(right_def))
    spoiler_arm = 1 if read_int("sim/cockpit2/switches/speedbrake_arm") else 0
    _write_u32(0x0BCC, 4800 if spoiler_arm else 0)
    log_debug(
        f"SPOILERS cmd_ratio={spoiler_ratio:.2f} left_deg={left_def:.1f} "
        f"right_deg={right_def:.1f}"
    )

    # Gear
    gear_handle = read_int("sim/cockpit2/controls/gear_handle_down")
    _write_u16(0x0BE8, 1 if gear_handle else 0)
    log_debug(f"GEAR HANDLE: {gear_handle}")
    has_retract = read_float("sim/aircraft/gear/acf_gear_retract")
    if has_retract >= 1.0:
        gear_flags = 1
    else:
        gear_flags = 0
    _write_u16(0x060C, gear_flags)
    _write_u16(0x060E, gear_flags)
    log_verbose(f"GEAR TYPE: retract_ref={has_retract:.1f} fsuipc={gear_flags}")
    deploy = read_array("sim/flightmodel/parts/gear_deploy", 3)
    deploy_offsets = (0x0C34, 0x0C30, 0x0C38)
    all_down = True
    for idx, off in enumerate(deploy_offsets):
        val = deploy[idx] if idx < len(deploy) else 0.0
        ratio = clamp(val, 0.0, 1.0)
        if ratio < 0.99:
            all_down = False
        _write_u16(off, int(ratio * 16383.0))
    _write_u16(0x0C3C, 16383 if all_down else 0)
    left = deploy[0] if len(deploy) > 0 else 0.0
    right = deploy[1] if len(deploy) > 1 else 0.0
    nose = deploy[2] if len(deploy) > 2 else 0.0
    log_debug(f"GEAR DEPLOY: mainL={left:.2f} mainR={right:.2f} nose={nose:.2f} all_down={all_down}")

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

    # Fuel / Weights
    fuel_total_kg = max(0.0, read_float("sim/flightmodel/weight/m_fuel_total"))
    fuel_capacity_kg = read_float_fallback(("sim/aircraft/weight/acf_m_fuel_tot",), 0.0)
    if fuel_capacity_kg <= 1.0:
        fuel_capacity_kg = 3000.0  # reasonable default to avoid zero-capacity edge cases
    fuel_pct = fuel_total_kg / fuel_capacity_kg
    fuel_pct = clamp(fuel_pct, 0.0, 1.0)
    fuel_units = int(clamp(fuel_pct, 0.0, 1.0) * 128.0 * 65536.0)
    _write_u32(0x0B7C, fuel_units)  # left main level
    _write_u32(0x0B94, fuel_units)  # right main level
    _write_u32(0x0B74, 0)           # center tank level (unused default)
    # Capacities in US gallons (distribute evenly across L/R, mirror to center)
    fuel_capacity_lbs = fuel_capacity_kg * KG_TO_LBS
    fuel_capacity_gal = fuel_capacity_lbs / FUEL_LBS_PER_GAL if fuel_capacity_lbs > 0.0 else 0.0
    per_tank_gal = fuel_capacity_gal / 2.0 if fuel_capacity_gal > 0.0 else 0.0
    cap_u32 = int(max(0.0, min(per_tank_gal, (2**32 - 1))) + 0.5)
    _write_u32(0x0B80, cap_u32)  # left main capacity
    _write_u32(0x0B98, cap_u32)  # right main capacity
    _write_u32(0x0B78, 0)        # center capacity unused by default
    _write_u16(0x0AF4, int(FUEL_LBS_PER_GAL * 256.0 + 0.5))

    empty_mass_kg = max(0.0, read_float_fallback((
        "sim/flightmodel/weight/m_fixed",
        "sim/aircraft/weight/acf_m_empty",
    ), 0.0))
    total_mass_kg = max(0.0, read_float_optional("sim/flightmodel/weight/m_total") or 0.0)
    payload_kg = max(0.0, total_mass_kg - fuel_total_kg - empty_mass_kg)
    zfw_kg = empty_mass_kg + payload_kg
    total_mass_kg = zfw_kg + fuel_total_kg
    max_gross_kg = read_float_fallback((
        "sim/flightmodel/weight/m_max",
        "sim/aircraft/weight/acf_m_max",
    ), 0.0)

    zfw_lbs = zfw_kg * KG_TO_LBS
    fuel_lbs = fuel_total_kg * KG_TO_LBS
    total_lbs = zfw_lbs + fuel_lbs
    payload_lbs = payload_kg * KG_TO_LBS
    max_gross_lbs = max_gross_kg * KG_TO_LBS if max_gross_kg > 0.0 else 0.0

    _write_f64(0x30C0, total_lbs)  # current loaded weight in lbs (FSUIPC spec)
    _write_f64(0x30C8, total_lbs / 32.174049 if total_lbs > 0.0 else 0.0)  # mass in slugs
    zfw_scaled = int(clamp(zfw_lbs, 0.0, (2**32 - 1) / 256.0) * 256.0 + 0.5)
    _write_u32(0x3BFC, zfw_scaled)
    if max_gross_lbs > 0.0:
        max_gross_scaled = int(clamp(max_gross_lbs, 0.0, (2**32 - 1) / 256.0) * 256.0 + 0.5)
        _write_u32(0x1334, max_gross_scaled)
        _write_f64(0x1260, max_gross_lbs)

    log_verbose(
        "WEIGHTS fuel=%.1fkg(%.1f%%) payload=%.1fkg/%.0flb zfw=%.1fkg/%.0flb gw=%.1fkg/%.0flb max_gw=%.1fkg/%.0flb"
        % (
            fuel_total_kg,
            fuel_pct * 100.0,
            payload_kg,
            payload_lbs,
            zfw_kg,
            zfw_lbs,
            total_mass_kg,
            total_lbs,
            max_gross_kg,
            max_gross_lbs,
        )
    )

    # Cabin signs (best effort)
    seatbelt_mode = _resolve_cabin_sign("seatbelt")
    nosmoke_mode = _resolve_cabin_sign("nosmoke")
    _write_u8(0x3414, int(seatbelt_mode))
    _write_u8(0x3415, int(nosmoke_mode))
    log_verbose(f"CABIN SIGNS seatbelt={seatbelt_mode} nosmoke={nosmoke_mode}")

    xpdr_code = clamp(read_int_fallback((
        "sim/cockpit2/radios/actuators/transponder_code",
        "sim/cockpit/radios/transponder_code",
    )), 0, 7777)
    encoded_code = encode_bcd4(int(xpdr_code), octal=True)
    _write_u16(0x0354, encoded_code)
    xpdr_mode = clamp(read_int_fallback((
        "sim/cockpit/radios/transponder_mode",
        "sim/cockpit2/radios/actuators/transponder_mode",
    )), 0, 4)
    if xpdr_mode <= 0:
        fs_mode = 0  # OFF
    elif xpdr_mode == 1:
        fs_mode = 1  # STBY
    elif xpdr_mode == 2:
        fs_mode = 3  # ON
    else:
        fs_mode = 4  # ALT
    _write_u8(0x0328, fs_mode)
    _write_u8(0x0B46, fs_mode)
    _write_u8(0x7B91, fs_mode)
    if _prev_xpdr_code != encoded_code or _prev_xpdr_mode != fs_mode:
        log_debug(f"XPDR code={xpdr_code:04d} encoded=0x{encoded_code:04X} mode={fs_mode}")
        _prev_xpdr_code = encoded_code
        _prev_xpdr_mode = fs_mode

    # Radios (COM1/COM2 active + standby)
    com1_active, src1a = _read_radio_frequency_debug("com1_active")
    com1_stby, src1s = _read_radio_frequency_debug("com1_stby")
    com2_active, src2a = _read_radio_frequency_debug("com2_active")
    com2_stby, src2s = _read_radio_frequency_debug("com2_stby")
    com1_active_bcd = encode_com_freq(com1_active)
    com1_stby_bcd = encode_com_freq(com1_stby)
    com2_active_bcd = encode_com_freq(com2_active)
    com2_stby_bcd = encode_com_freq(com2_stby)
    _write_u16(0x034E, com1_active_bcd)
    _write_u16(0x311A, com1_stby_bcd)
    _write_u16(0x3118, com2_active_bcd)
    _write_u16(0x311C, com2_stby_bcd)
    log_verbose(
        "COM RADIOS com1=%.3f(0x%04X)/%.3f(0x%04X) com2=%.3f(0x%04X)/%.3f(0x%04X)"
        % (
            com1_active,
            com1_active_bcd,
            com1_stby,
            com1_stby_bcd,
            com2_active,
            com2_active_bcd,
            com2_stby,
            com2_stby_bcd,
        )
    )
    log_debug(
        f"COM SRC com1_act={src1a or 'none'} com1_stby={src1s or 'none'} "
        f"com2_act={src2a or 'none'} com2_stby={src2s or 'none'}"
    )

    # Avionics master
    avionics_sources = read_int_array("sim/cockpit2/switches/avionics_power_on", 2)
    avionics_on = 1 if any(avionics_sources) else read_int("sim/cockpit/electrical/avionics_on")
    _write_u32(0x2E80, 1 if avionics_on else 0)
    log_verbose(f"AVIONICS power={avionics_on}")

    # Battery master
    battery_sources = read_int_array("sim/cockpit2/electrical/battery_on", 4)
    battery_on = 1 if any(battery_sources) else read_int("sim/cockpit/electrical/battery_on")
    _write_u32(0x281C, 1 if battery_on else 0)
    log_verbose(f"BATTERY power={battery_on}")

    # Altimeter / barometer settings
    baro_inhg = read_float_fallback((
        "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot",
        "sim/cockpit/misc/barometer_setting",
    ), 29.92)
    baro_hpa = read_float_fallback((
        "sim/cockpit2/gauges/actuators/barometer_setting_hpa_pilot",
    ), baro_inhg * 33.8638866667)
    _write_u16(0x0330, int(clamp(baro_hpa, 0.0, 2000.0) * 16.0 + 0.5))
    _write_u16(0x0332, int(clamp(baro_inhg, 0.0, 60.0) * 16.0 + 0.5))

    standby_baro_hpa = read_float_optional("sim/cockpit2/gauges/actuators/barometer_setting_hpa_copilot")
    if standby_baro_hpa is None:
        standby_baro_hpa = baro_hpa
    standby_baro_inhg = read_float_optional("sim/cockpit2/gauges/actuators/barometer_setting_in_hg_copilot")
    if standby_baro_inhg is None:
        standby_baro_inhg = baro_inhg
    _write_u16(0x3542, int(clamp(standby_baro_hpa, 0.0, 2000.0) * 16.0 + 0.5))

    standby_alt_ft = read_float_optional("sim/cockpit2/gauges/indicators/altitude_ft_copilot")
    if standby_alt_ft is None:
        standby_alt_ft = indicated_alt_ft
    _write_s32(0x3544, int(standby_alt_ft))

    log_verbose(
        f"ALTIMETER main={baro_hpa:.1f} hPa/{baro_inhg:.2f} inHg alt={indicated_alt_ft:.0f}ft "
        f"stdby={standby_baro_hpa:.1f} hPa/{standby_baro_inhg:.2f} inHg alt={standby_alt_ft:.0f}ft"
    )

    # G-force (normal)
    g_force = clamp(read_float("sim/flightmodel2/misc/gforce_normal"), -8.0, 8.0)
    g_units = int(g_force * 625.0)
    _write_int(0x11BA, g_units, 2, signed=True)
    _write_int(0x11B8, g_units, 2, signed=True)

    # Wind (ambient + surface layer)
    ambient_speed_knots = clamp(read_float("sim/weather/aircraft/wind_now_speed_msc") * 1.943844, 0.0, 65535.0)
    ambient_dir_true = read_float("sim/weather/aircraft/wind_now_direction_degt")
    # Deprecated global arrays removed; rely on aircraft + region datarefs only
    _write_u16(0x0E90, int(ambient_speed_knots + 0.5))
    _write_u16(0x0E92, encode_direction16(ambient_dir_true))

    surface_region_speed_arr = read_array_range("sim/weather/region/wind_speed_kt", 0, 1)
    surface_region_dir_arr = read_array_range("sim/weather/region/wind_direction_degt", 0, 1)
    surface_region_top_arr = read_array_range("sim/weather/region/wind_altitude_msl_m", 0, 1)
    surface_region_speed = first(surface_region_speed_arr, 0.0)
    surface_region_dir = first(surface_region_dir_arr, 0.0)
    surface_region_top_msl = first(surface_region_top_arr, 0.0)
    surface_speed_knots = clamp(surface_region_speed if surface_region_speed > 0.0 else ambient_speed_knots, 0.0, 65535.0)
    surface_dir_true = surface_region_dir if surface_region_dir != 0.0 else ambient_dir_true
    surface_dir_mag = surface_dir_true - mag_var  # convert True → Magnetic (positive variation = East)
    surface_dir_u16 = encode_direction16(surface_dir_mag)
    surface_ceiling_agl = max(0.0, surface_region_top_msl - ground_alt_m)
    _write_u16(0x0EEE, int(min(surface_ceiling_agl, 65535.0) + 0.5))
    _write_u16(0x0EF0, int(surface_speed_knots + 0.5))
    _write_u16(0x0EF2, surface_dir_u16)

# parse FS6IPC block

def parse_ipc_block(data: bytearray) -> bytearray:
    update_snapshot()
    pos = 0
    end = len(data)
    log_debug(f"parse_ipc_block size={end}")
    while pos + 4 <= end:
        cmd = int.from_bytes(data[pos:pos+4], "little")
        next_bytes = bytes_to_hex(data[pos:pos+16])
        log_debug(f"  block cmd=0x{cmd:08X} pos=0x{pos:04X} next={next_bytes}")
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
        log_debug(traceback.format_exc().strip())
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
            log_debug(f"dispatch cmd={cmd}")
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
            log(f"dispatch error: {e}")
            log_debug(traceback.format_exc().strip())
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
        _show_toast(f"wineUIPC connected {addr[0]} -> {HOST}:{PORT}")
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

    log_debug(f"recv payload keys={list(payload.keys())}")

    ev = threading.Event()
    req = Request(payload=payload, event=ev)
    REQ_QUEUE.put(req)

    if not ev.wait(timeout=REPLY_TIMEOUT):
        log(f"ipc timeout cmd={payload.get('cmd')} dwData={payload.get('dwData')} cbData={payload.get('cbData')} keys={list(payload.keys())}")
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
_handshake_logged = False


def XPluginStart():
    log(f"start module={__file__}")
    global _flightloop, _toast_draw_registered
    _flightloop = xp.createFlightLoop(_flightloop_cb)
    xp.scheduleFlightLoop(_flightloop, FLIGHTLOOP_INTERVAL, True)
    if xp.registerDrawCallback(_toast_draw, xp.Phase_Window, 0, None):
        _toast_draw_registered = True
    return PLUGIN_NAME, PLUGIN_SIG, PLUGIN_DESC


def XPluginStop():
    log("stop")
    global _toast_draw_registered
    if _toast_draw_registered:
        try:
            xp.unregisterDrawCallback(_toast_draw, xp.Phase_Window, 0, None)
        except Exception:
            pass
        _toast_draw_registered = False


def XPluginEnable():
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        log_debug("server thread already running, skipping restart")
    else:
        _server_stop.clear()
        _server_thread = threading.Thread(target=_serve, daemon=True)
        _server_thread.start()
        log("server thread started")
    return 1


def XPluginDisable():
    global _server_socket, _server_thread
    _server_stop.set()
    global _toast_draw_registered
    if _toast_draw_registered:
        try:
            xp.unregisterDrawCallback(_toast_draw, xp.Phase_Window, 0, None)
        except Exception:
            pass
        _toast_draw_registered = False
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
