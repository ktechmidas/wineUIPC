# fakeFSUIPC / pyUIPC Bridge

> Alpha-stage toolchain that lets **A Pilot’s Life – Chapter 2** talk to **X-Plane** through a lightweight, Wine-friendly replacement for FSUIPC.

The repository contains two cooperating components:

1. **`uipc_bridge`** – a Windows executable (can be run via Wine/Proton) that exposes the same WM_COPYDATA and shared-memory interface as the legacy FSUIPC DLL. Instead of talking to a real simulator it forwards requests over TCP as JSON.
2. **`pyUIPC`** – an XPPython3 plugin that runs inside X-Plane, receives the JSON requests, populates FSUIPC-style memory offsets from X-Plane datarefs, and sends the reply block back to the bridge.

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
| Transponder                 | ✅     | Squawk at 0x0354, mode mirrored to 0x0B46 and 0x7B91. |
| Wind & weather              | ✅     | Surface layer speed/direction. |
| Slew / pause detection      | ⏳     | Pause wired; slew flag still TODO. |

---

## Versioning & Release Strategy

We follow a SemVer-inspired scheme while the project is in alpha:

| Stage        | Tag format        | Criteria                                                                 |
|--------------|-------------------|--------------------------------------------------------------------------|
| Alpha builds | `v0.<minor>.<patch>-alpha.<n>` | Any change that affects functionality or data mappings. Fast cadence. |
| Beta         | `v0.<minor>.<patch>-beta.<n>`  | Offset coverage frozen, only bug fixes.                                |
| Stable       | `v1.<minor>.<patch>`           | Full feature set for APL2; braking changes follow SemVer rules.       |

Release checklist:

1. Update `pyUIPC/main.py` and/or `uipc_bridge.c`.
2. Run regression flight (startup → taxi → flight → landing) and verify `python.txt` contains no unresolved errors.
3. Update the changelog (see below) and README feature matrix if scope changed.
4. Tag the repository and attach the built `uipc_bridge.exe` + relevant logs/zips.

A simple `CHANGELOG.md` template (to add later):

```markdown
## [v0.1.0-alpha.1] - 2025-11-10
### Added
- Initial public alpha: TCP bridge + IAS/engine data.
### Fixed
- ...
```

---

## Architecture Overview

```
┌──────────────────────┐         TCP JSON          ┌──────────────────────┐
│  Windows client      │  WM_COPYDATA / shared     │  pyUIPC (XPPython3)  │
│  (APL2 via Wine) ───►│  memory → uipc_bridge ───►│  + X-Plane datarefs  │
└──────────────────────┘                            └──────────────────────┘
```

1. APL2 issues normal FSUIPC IPC blocks.
2. `uipc_bridge` collects the block, encodes it as JSON (`{"cmd":"ipc", ...}`) and forwards it to the Python plugin.
3. `pyUIPC` snapshots the required datarefs, mutates the IPC buffer per FSUIPC rules, and returns a hexadecimal payload.
4. Replies travel back over TCP and are written into the original shared memory region so the Windows client thinks FSUIPC answered natively.

---

## Getting Started

1. **Build the bridge (optional):**
   ```bash
   x86_64-w64-mingw32-gcc -O2 -municode uipc_bridge.c -lws2_32 -o uipc_bridge.exe
   ```
   (Pre-built binaries live in `uipc_bridge.exe`.)

2. **Prepare Wine for `uipc_bridge`:**
   - Use a clean 64-bit wineprefix.
   - Install the required .NET runtimes and fonts (e.g. via `winetricks`):
     ```
     winetricks dotnet35 dotnet40 dotnet45 dotnet46 dotnet461 dotnet462 dotnet471 dotnet48 dotnet7 allfonts
     ```
   - Reboot the prefix (`wineboot -r`) after the installs.

3. **Install `pyUIPC`:**
   - Copy `pyUIPC/` to `X-Plane 12/Resources/plugins/PythonPlugins/`.
   - Ensure XPPython3 is installed and enabled.

4. **Run the stack:**
   - Start X-Plane → verify XPPython3 log shows `xpc_ipc enable`.
   - Launch `uipc_bridge.exe` via Wine from this repository directory.
   - Start APL2; it should detect FSUIPC and show live data.

4. **Logs:**
   - `python.txt` (in the repository root) records every snapshot plus custom diagnostic lines (e.g., transponder updates).
   - `uipc_bridge.log` captures socket/IPC issues on the Windows side.

---

## Roadmap
- [ ] Finish transponder compatibility for APL2 (confirm which offset it reads and mirror accordingly).
- [ ] Validate overspeed detection against APL2 scoring.
- [ ] Investigate Toliss A320 datarefs for seatbelt/no-smoking signs and mirror into 0x3414/0x3415.
- [ ] Publish gear handle + per-wheel states (offsets 0x0BE8/0x0BF0+) and add dedicated “gear up/down” logging.
- [ ] Determine flags/offsets for “retractable gear”, “spoilers available”, “flaps available”, “strobes available”.
- [ ] Implement crash indicator (offset 0x0840) and verify behaviour in APL2.
- [ ] Populate touchdown time (0x030F) alongside landing rate to match FSUIPC behaviour.
- [ ] Expose wind layers beyond surface (upper/middle) plus turbulence for more realism.
- [ ] Research an X-Plane 12 dataref/strategy for “slew mode” (e.g. `sim/operation/override/override_slew`) so we can map offset 0x05DC instead of leaving it unsupported.
- [ ] Automate regression flights via replay or scripted XP tools.
- [ ] Enhance `uipc_bridge` UI: add “Connected” status text and a dedicated “Close/Exit” button that stops the message loop cleanly.
- [ ] Add a “Restart Bridge” button to `uipc_bridge` to rebind the socket without closing Wine.
- [ ] Update simulator identification strings (e.g. replace “P3D” with “X-Plane”) wherever the bridge reports the platform to clients/logs.
- [ ] Review altitude source: switch from true elevation to the same indicated altitude used by cockpit instruments if FSUIPC clients expect that.

---

## Contributing

1. Fork and create a feature branch (`feat/<name>` or `fix/<name>`).
2. Run X-Plane with the updated plugin and attach relevant snippets from `python.txt`/`uipc_bridge.log` when opening a PR.
3. Keep Python changes formatted (PEP8-ish) and prefer small dedicated commits per feature.

---

## License

_TBD – insert MIT/BSD/GPL notice once we settle on the licensing model._

---

For questions or debugging help drop the latest `python.txt` excerpt plus your APL2 symptom into an issue. Happy flying! ✈️
