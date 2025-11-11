# FSUIPC Offset Status

## Abgedeckte Offsets

| Offset | Größe | Beschreibung | Status | Quelle / DataRef(s) |
|--------|-------|--------------|--------|----------------------|
| 0x3304–0x3364 | 2–4 | Handshake / FS6IPC IDs | ✅ Implementiert | Feste Werte in `update_snapshot` |
| 0x0560 / 0x0568 | 8 | Latitude / Longitude (FS units) | ✅ Implementiert | `sim/flightmodel/position/latitude`, `.../longitude` |
| 0x0570 | 8 | Altitude (Metres AMSL) | ✅ Implementiert | `sim/flightmodel/position/elevation` |
| 0x0578 / 0x057C | 4 | Pitch / Bank | ✅ Implementiert | `sim/flightmodel/position/theta`, `phi` |
| 0x0580 | 4 | Heading | ✅ Implementiert | `sim/cockpit/autopilot/heading_mag` (Fallback `sim/flightmodel/position/psi`) |
| 0x02B4 / 0x02B8 | 4 | Ground speed / TAS | ✅ Implementiert | `sim/flightmodel/position/groundspeed`, `true_airspeed` |
| 0x02BC | 4 | IAS (Knots ×128) | ✅ Implementiert | `sim/cockpit2/gauges/indicators/airspeed_kts_pilot` |
| 0x02C4 / 0x02C8 | 4 | Barber pole / Vertical speed | ✅ Implementiert | Fixwert (Barber pole), `sim/flightmodel/position/vh_ind_fpm` |
| 0x030C | 2 | Landing rate (ft/min) | ✅ Implementiert | Durchschnitt der letzten `vh_ind_fpm`-Samples vor Touchdown |
| 0x0366 / 0x036C / 0x036D | 1 | On ground / Stall / Overspeed | ✅ Implementiert | `sim/flightmodel/failures/onground_any`, `sim/flightmodel2/misc/...` |
| 0x0020 / 0x0B4C | 4 / 2 | Ground altitude | ✅ Implementiert | `elevation - y_agl` |
| 0x0280 / 0x0281 / 0x028C | 1 | Lights | ✅ Implementiert | `sim/cockpit2/switches/...` |
| 0x0D0C | 2 | Combined light bitfield | ✅ Implementiert | Aus Einzelbits zusammengesetzt |
| 0x0BC8 | 2 | Parking brake | ✅ Implementiert | `sim/flightmodel/controls/parkbrake` |
| 0x0BDC–0x0BE4 | 4 | Flappositionen | ✅ Implementiert | `sim/flightmodel/controls/flaprat` |
| 0x0BD0–0x0BD8 | 4 | Spoilerpositionen | ✅ Implementiert | `sim/flightmodel/controls/sbrkrat` |
| 0x0BCC | 4 | Spoiler arm | ✅ Implementiert | `sim/cockpit2/switches/speedbrake_arm` |
| 0x0BE8 / 0x0BF0.. | 2 / 4 | Gear handle & deploy ratios | ✅ Implementiert | `sim/cockpit2/controls/gear_handle_down`, `sim/flightmodel2/gear/deploy_ratio` |
| 0x0894, 0x092C, 0x09C4, 0x0A5C + | 2 | Engine combustion / N1 / N2 | ✅ Implementiert | `sim/flightmodel/engine/ENGN_running`, `ENGN_N1_`, `ENGN_N2_` |
| 0x0AEC | 2 | Engine count | ✅ Implementiert | `sim/aircraft/prop/acf_num_engines` |
| 0x0B7C / 0x0B94 | 4 | Fuel proxy | ✅ Implementiert | `sim/flightmodel/weight/m_fuel_total` |
| 0x3414 / 0x3415 | 1 | Seatbelt / No smoking | ✅ Implementiert (prüfen Toliss) | `sim/cockpit2/switches/...` |
| 0x0E90 / 0x0E92 / 0x0EF0 / 0x0EF2 | 2 | Surface wind speed/dir | ✅ Implementiert | `sim/weather/wind_speed_kt`, `.../wind_direction_degt` |
| 0x11B8 / 0x11BA | 2 | G-Force | ✅ Implementiert | `sim/flightmodel2/misc/gforce_normal` |
| 0x0354 | 2 | Transponder squawk | ✅ Implementiert | `sim/cockpit2/radios/actuators/transponder_code` |
| 0x0B46 / 0x7B91 | 1 | Transponder mode | ✅ Implementiert | `sim/cockpit2/radios/actuators/transponder_mode` |
| 0x0262 / 0x0264 | 2 | Pause control/indicator | ✅ Implementiert | `sim/time/paused` |

## Offene Offsets / TODO

| Offset | Größe | Beschreibung | Status | Vorschlag |
|--------|-------|--------------|--------|-----------|
| 0x030F | 2 | Touchdown timestamp | ❌ Offen | `sim/time/local_time_sec` beim Touchdown |
| 0x0840 | 2 | Crash indicator | ❌ Testen | `sim/operation/failures/rel_fail`, `sim/operation/runway_status/crashed` |
| 0x060E | 2 | Retractable gear flag | ⚠️ Fake | `sim/aircraft/overflow/acf_gear_retract` |
| 0x0778 / 0x078C / 0x0794 | 4 | Flaps/Spoilers/Strobes available | ⚠️ Fake | `sim/aircraft/.../acf_has_flap/spoiler/strobe` |
| 0x02A0 | 2 | Magnetic variation | ✅ Implementiert | `sim/flightmodel/position/magnetic_variation` |
| 0x02CC | 8 | Compass heading | ❌ Offen | `sim/cockpit2/gauges/indicators/heading_electric_deg_mag_pilot` |
| 0x0330 | 2 | Altimeter (Kollsman) | ❌ Offen | `sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot` |
| 0x05DC | 2 | Slew mode indicator/control | ❌ Offen | `sim/operation/override/override_slew` oder eigener Flag |
| 0x084C | 2 | Crash/reset flag | ❌ Offen | Zusammen mit 0x0840 testen |
| 0x0F4Cff | 2 | Upper/mid/lower wind layers | ❌ Offen | `sim/weather/wind_*` Index 1–3 + Altitude fields |
| 0x0300–0x0338 | … | Flight stage helpers | ❓ Klären | Prüfen, ob APL2 liest |
| 0x0B80 | 4 | Payload / ZFW | ❌ Offen | `sim/flightmodel/weight/m_fixed`, `m_total` |
| 0x0898+ (Fuel flow, torque, oil) | 2/4 | Engine telemetry | Optional | `sim/flightmodel/engine/...` |
| 0x02FA / 0x07xx | 2/4 | Autopilot states | ❌ Offen | `sim/cockpit/autopilot/...` |
| 0x0848ff | 2 | Landing lights availability etc. | ❓ | Prüfen Relevanz |
| 0x3414 / 0x3415 | 1 | Seatbelt/NoSmoking (Toliss spezifisch) | ⚠️ Prüfen | Toliss DataRefs (`toliss_airbus/...`) |

Ergänze Zeilen nach Bedarf, speziell wenn neue Offsets in den Kommunikationsblöcken auftauchen.
