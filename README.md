# wineUIPC Bridge

> Alpha-stage toolchain that lets an **FSUIPC client** under Wine like **A Pilot’s Life – Chapter 2** talk to **X-Plane** through a lightweight, Wine-friendly replacement for FSUIPC.

The repository contains two cooperating components:

1. **`uipc_bridge`** – a Windows executable (can be run via Wine/Proton) that exposes the same WM_COPYDATA and shared-memory interface as the legacy FSUIPC DLL. Instead of talking to a real simulator it forwards requests over TCP as JSON.
2. **`wineUIPC`** – an XPPython3 plugin that runs inside X-Plane, receives the JSON requests, populates FSUIPC-style memory offsets from X-Plane datarefs, and sends the reply block back to the bridge.

Together they allow Windows-only tooling (APL2, FSUIPC clients, etc.) to operate on Linux/macOS setups without XPUIPC while giving us full control over which offsets are simulated.

---

## Project Goals

- **Compatibility:** Provide the subset of FSUIPC offsets that APL2 and common tooling rely on (position, handshake, flight controls, lights, engines, etc.).
- **Deterministic Lifecycle:** Allow rapid reloads of the XPPython plugin and reconnections from Wine without stuck sockets or stale shared-memory handles.
- **Extensibility:** Keep the Python side readable and dataref-driven so additional offsets can be mapped quickly.
- **Cross-platform:** Require no native code on the X-Plane host; only the bridge executable needs Wine.

---

## Current Feature Matrix (alpha)

| Area                         | Status | Notes |
|-----------------------------|--------|-------|
| FSUIPC handshake & stages   | ✅     | Offsets 0x3304–0x3364 implemented. |
| Position & attitude         | ✅     | Lat/Lon/Alt + pitch/bank/heading in FS units. |
| Airspeed / groundspeed      | ✅     | IAS now sourced from `sim/cockpit2/gauges/indicators/airspeed_kts_pilot`. |
| Stall / overspeed / G-force | ✅     | Derived from `sim/flightmodel2` ratios. |
| Lights & switches           | ✅     | Individual bits plus aggregated mask (0x0D0C). |
| Flaps / spoilers / gear     | ✅     | Includes spoiler arm and gear ratios. |
| Engines                     | ✅     | 1–4 engines, combustion + N1/N2 + engine count (0x0AEC). |
| Fuel & weights              | ✅     | Fuel levels/capacities (0x0B74/0x0B7C/0x0B94/0x0B80/0x0B98) + ZFW/MTOW/gross in lbs (0x3BFC/0x1334/0x30C0). |
| Transponder                 | ✅     | Squawk at 0x0354, mode mirrored to 0x0B46 and 0x7B91. |
| Wind & weather              | ✅     | Ambient wind from `sim/weather/aircraft/wind_now_*`, surface layer from `sim/weather/region/wind_*[0]`. |
| Altimeter / QNH             | ✅     | Both hPa (0x0330) and inHg (0x0332) mirrored from the pilot’s baro setting. |
| Radios (COM1/COM2)          | ✅     | Active + standby FSUIPC offsets populated from X-Plane’s radio stack. |
| Electrical (battery/avionics) | ✅   | Master switches mirrored to 0x281C (battery) and 0x2E80 (avionics). |
| Slew / pause detection      | ⏳     | Pause wired; slew flag still TODO. |

> Limitation: X-Plane does not expose a reliable “has spoilers” flag, so the FSUIPC availability offsets (0x0778/0x078C/0x0794) remain deprecated and are left unset.

See `CABIN_SIGNS.md` for the evolving list of cabin-sign datarefs (default + popular add-ons).

## Handshake / version mapping

`wineUIPC` lets you pick the advertised simulator and FSUIPC version via `wineUIPC.cfg`:

- `fs_version` → value written to offset `0x3308` (FS version code)
- `fsuipc_version` → BCD version for offset `0x3304` HIWORD (e.g. `7.505` → `0x7505`)
- `fsuipc_build_letter` → build letter (blank or `a`-`z`; `a` = 1, etc.)

Environment overrides (quick tests without editing the file): `XPC_FS_VERSION`, `XPC_FSUIPC_VERSION`, `XPC_FSUIPC_BUILD`.

Known working/observed pairs:

| Scenario / client target          | `fs_version` (0x3308) | `fsuipc_version` (0x3304 hiword) | `fsuipc_build_letter` | Notes |
|-----------------------------------|-----------------------|-----------------------------------|------------------------|-------|
| MSFS 2024 (observed)              | 14                    | 7.505 (`0x7505`)                  | _(blank)_              | Stable with APL2 on Linux ↔ Wine. |
| X-Plane 12 + XPUIPC (Windows obs) | 8                     | 5.000 (`0x5000`)                  | h (8)                  | Matches Windows XPUIPC capture. |
| Prepar3D fallback (alpha1)        | 10                    | 1.998 (`0x1998`)                  | e (5)                  | Legacy combo that worked in alpha1. |
| FS2004 compatibility              | 7                     | 3.820 (`0x3820`)                  | a (1)                  | For FSUIPC3-era clients. |

If a client is picky about versions, pick the closest match from the table and adjust in `wineUIPC.cfg` (or via the env vars) before starting X-Plane.

### Altimeter / baro offsets (FSUIPC layout)

- Main altimeter pressure: `0x0330` (hPa×16), `0x0332` (inHg×16)
- Main indicated altitude: `0x3324` (feet, per FSUIPC spec)
- Standby altimeter pressure: `0x3542` (hPa×16) — falls back to main if no copilot dataref
- Standby indicated altitude: `0x3544` (feet) — falls back to main if no copilot dataref

--- 
## Changelog

```markdown
## [v0.1.0-alpha.5] - 2025-12-21
### Added
- Standby altimeter offsets populated: 0x3542 (baro hPa×16) and 0x3544 (standby altitude, feet), with fallback to main altimeter when no copilot dataref exists.
- Altimeter/baro offset summary in README for main + standby mappings.
### Known / Testing
- Validate with clients sensitive to QNH/STD transitions (e.g., FSAirlines) to confirm reduced mismatch between indicated and true altitudes.

## [v0.1.0-alpha.4] - 2025-12-15
### Added
- Configurable FSUIPC/FS handshake via `wineUIPC.cfg` (fs_version, fsuipc_version, fsuipc_build_letter) and env overrides (`XPC_FS_VERSION`, `XPC_FSUIPC_VERSION`, `XPC_FSUIPC_BUILD`).
- Handshake/version mapping table for common client targets (MSFS2024, X-Plane+XPUIPC, P3D fallback, FS2004).
- Overspeed and stall warning flags now also honor X-Plane annunciators/prefs (`sim/operation/prefs/warn_overspeed`, `sim/cockpit2/annunciators/stall_warning`).
### Known / Testing
- Continue manual checks with APL2; MSFS2024/FSUIPC7 profile is the stable default. Other version combos depend on client tolerance.

## [v0.1.0-alpha.3] - 2025-12-14
### Added
- Radio altitude (AGL) exposed via FSUIPC offset 0x31E4 using X-Plane `y_agl`.
- Simulation rate mirrored to FSUIPC offset 0x0C1A from X-Plane sim speed datarefs.
- Bridge CLI gains `-v/--verbose` logging with timestamps; `--host/--port` can override plugin address when needed.
### Fixed
- FSUIPC handshake now reports 3.820 (FS2004) for better compatibility with FSAirlines.
- Gross/zero fuel weight calculation aligned with X-Plane empty/payload/fuel parts; max gross uses `m_max` fallback and is mirrored to both 0x1334 (lbs×256) and 0x1260 (lbs).
### Known / Testing
- No automated tests; run manual checks (cold start → taxi → landing) with APL2/FSAirlines. Bridge reconnect and verbose logging still need validation on your setup.

## [v0.1.0-alpha.2] - 2025-11-17
### Added
- Fuel/weight mapping (levels, capacities, ZFW/MTOW/gross) for FSUIPC clients.
- More explicit COM1/COM2 radio logging and encoding to FSUIPC BCD.
### Fixed
- COM BCD encoding snapped to FSUIPC’s expected 4-digit format (leading “1” stripped).
- Fuel level duplication (center tank) removed; correct percent now reported.
```

---

## Architecture Overview

```
┌──────────────────────┐         TCP JSON          ┌──────────────────────┐
│  Windows client      │  WM_COPYDATA / shared     │  wineUIPC (XPPython3)  │
│  (APL2 via Wine) ───►│  memory → uipc_bridge ───►│  + X-Plane datarefs  │
└──────────────────────┘                           └──────────────────────┘
```

1. APL2 issues normal FSUIPC IPC blocks.
2. `uipc_bridge` collects the block, encodes it as JSON (`{"cmd":"ipc", ...}`) and forwards it to the Python plugin.
3. `wineUIPC` snapshots the required datarefs, mutates the IPC buffer per FSUIPC rules, and returns a hexadecimal payload.
4. Replies travel back over TCP and are written into the original shared memory region so the Windows client thinks FSUIPC answered natively.
5. If ACARS tools expect a livery name, we mirror the active livery string into FSUIPC’s aircraft-name offsets (0x313C/0x3160) with the current X-Plane selection.

---

## Getting Started

1. **Build the bridge (optional):**
   ```bash
   x86_64-w64-mingw32-gcc -O2 uipc_bridge.c -lws2_32 -lgdi32 -o uipc_bridge.exe
   ```
   (Pre-built binaries live in `uipc_bridge.exe`.)

2. **Prepare Wine for `uipc_bridge`:**
   - Use a clean 64-bit wineprefix.
   - Install the required .NET runtimes and fonts (e.g. via `winetricks`):
     ```
     winetricks dotnet35 dotnet40 dotnet45 dotnet46 dotnet461 dotnet462 dotnet471 dotnet48 dotnet7 allfonts
     ```
   - Reboot the prefix (`wineboot -r`) after the installs.

3. **Install `wineUIPC`:**
   - Copy `wineUIPC/` to `X-Plane 12/Resources/plugins/PythonPlugins/`.
   - Ensure XPPython3 is installed and enabled.

4. **Run the stack:**
   - Start X-Plane → verify XPPython3 log shows `xpc_ipc enable`.
   - Launch `uipc_bridge.exe` via Wine from this repository directory. The window now shows a live connection status plus `Restart Bridge` (forces a reconnect to the plugin) and `Close` buttons.
   - Start APL2; it should detect FSUIPC and show live data.

4. **Logs:**
   - `python.txt` (repo root) is XPPython3’s standard log—use it for crash reports or plugin load errors.
   - `uipc_bridge.log` captures socket/IPC issues on the Windows side.
   - `wineUIPC.log` (same folder as the plugin) is the plugin’s structured log. Control verbosity via `wineUIPC.cfg`:
     ```ini
     log_level=0  # 0=off, 1=verbose, 2=debug
     ```
     Set `log_level=2` when chasing dataref mappings (gear, winds, cabin signs) and attach excerpts to PRs.

---

## Roadmap
- [x] Finish transponder compatibility for APL2 (confirm which offset it reads and mirror accordingly).
- [ ] Validate overspeed detection against APL2 scoring.
- [x] Investigate Toliss A320 + popular add-on datarefs for seatbelt/no-smoking signs and mirror into 0x3414/0x3415.
- [x] Publish gear handle + per-wheel states (offsets 0x0BE8/0x0BF0+) and add dedicated “gear up/down” logging.
- [x] Determine flags for “retractable gear” (0x060C/0x060E) using `sim/aircraft/gear/acf_gear_retract`.
- [ ] Determine flags/offsets for “flaps available” and “strobes available”. Spoiler availability is currently blocked because no X-Plane dataref exposes a “has spoilers” capability flag.
- [x] Implement crash indicator (offset 0x0840) using `sim/flightmodel/failures/over_g` + `sim/flightmodel/failures/onground_any`.
- [x] Fuel/weight mapping (levels/capacities, ZFW, MTOW, gross) for FSUIPC clients.
- [ ] Expose wind layers beyond surface (upper/middle) plus turbulence for more realism.
- [ ] Automate regression flights via replay or scripted XP tools.
- [x] Enhance `uipc_bridge` UI: add “Connected” status text and a dedicated “Close/Exit” button that stops the message loop cleanly.
- [x] Add a “Restart Bridge” button to `uipc_bridge` to rebind the socket without closing Wine.
- [ ] Update simulator identification strings (e.g. replace “P3D” with “X-Plane”) wherever the bridge reports the platform to clients/logs.
- [ ] Gear offsets (0x060C/0x060E/0x0C30ff) are implemented but still untested in APL2—verify with multiple aircraft.
- [x] Expand engine telemetry (fuel flow, oil temp/pressure, etc.) beyond the basic offsets already published.
- [x] Add simple logging configuration (`wineUIPC.cfg` + `wineUIPC.log`) to the documentation once finalised.

---

## Contributing

1. Fork and create a feature branch (`feat/<name>` or `fix/<name>`).
2. Reproduce your change with X-Plane + APL2 running, then attach:
   - `wineUIPC.log` (set `log_level=2` before testing for detailed offsets),
   - screenshots or client behaviour notes if applicable.
3. Keep Python changes formatted (PEP8-ish), include inline comments for non-obvious dataref mappings, and prefer small dedicated commits per feature.

---

For questions or debugging help drop the latest `wineUIPC.log` excerpt plus your APL2 symptom into an issue. Happy flying! ✈️
