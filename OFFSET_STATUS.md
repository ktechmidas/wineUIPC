# FSUIPC Offset Status

## Covered Offsets

| Offset | Size | Description | Status | Source / DataRef(s) |
|--------|------|-------------|--------|----------------------|
| 0x3304–0x3364 | 2–4 | Handshake / FS6IPC IDs | ✅ Implemented | Fixed values in `update_snapshot` |
| 0x0C1A | 2 | Simulation rate ×256 | ✅ Implemented | `sim/time/sim_speed_actual` fallback `sim/time/sim_speed`, `sim/time/sim_rate` |
| 0x31E4 | 4 | Radio altitude (metres ×65536) | ✅ Implemented | `sim/flightmodel/position/y_agl` |
| 0x0560 / 0x0568 | 8 | Latitude / Longitude (FS units) | ✅ Implemented | `sim/flightmodel/position/latitude`, `.../longitude` |
| 0x0570 | 8 | Altitude (metres AMSL) | ✅ Implemented | `sim/flightmodel/position/elevation` |
| 0x0578 / 0x057C | 4 | Pitch / Bank | ✅ Implemented | `sim/flightmodel/position/theta`, `phi` |
| 0x0580 | 4 | Heading | ✅ Implemented | `sim/cockpit/autopilot/heading_mag` (fallback `sim/flightmodel/position/psi`) |
| 0x02B4 / 0x02B8 | 4 | Ground speed / TAS | ✅ Implemented | `sim/flightmodel/position/groundspeed`, `true_airspeed` |
| 0x02BC | 4 | IAS (knots ×128) | ✅ Implemented | `sim/cockpit2/gauges/indicators/airspeed_kts_pilot` |
| 0x02C4 / 0x02C8 | 4 | Barber pole / Vertical speed | ✅ Implemented | Fixed value (barber pole), `sim/flightmodel/position/vh_ind_fpm` |
| 0x02CC | 8 | Whiskey compass (deg, float64) | ✅ Implemented | `sim/cockpit2/gauges/indicators/heading_electric_deg_mag_pilot` (fallback `heading_mag`) |
| 0x030C | 4 | Landing rate (signed 256×m/s) | ✅ Implemented | Current vertical speed in flight, frozen on ground transition |
| 0x0366 / 0x036C / 0x036D | 1 | On ground / Stall / Overspeed | ✅ Implemented | `sim/flightmodel/failures/onground_any`, `sim/flightmodel2/misc/...` |
| 0x0020 / 0x0B4C | 4 / 2 | Ground altitude | ✅ Implemented | `elevation - y_agl` |
| 0x0280 / 0x0281 / 0x028C | 1 | Lights | ✅ Implemented | `sim/cockpit2/switches/...` |
| 0x0D0C | 2 | Combined light bitfield | ✅ Implemented | Composed from individual bits |
| 0x0BC8 | 2 | Parking brake | ✅ Implemented | `sim/flightmodel/controls/parkbrake` |
| 0x0BDC–0x0BE4 | 4 | Flap positions | ✅ Implemented | `sim/flightmodel/controls/flaprat` |
| 0x0BD0–0x0BD8 | 4 | Spoiler positions | ✅ Implemented | `sim/flightmodel/controls/sbrkrat` |
| 0x0BCC | 4 | Spoiler arm | ✅ Implemented | `sim/cockpit2/switches/speedbrake_arm` |
| 0x0BE8 / 0x0BF0.. | 2 / 4 | Gear handle & deploy ratios | ✅ Implemented | `sim/cockpit2/controls/gear_handle_down`, `sim/flightmodel2/gear/deploy_ratio` |
| 0x060C / 0x060E | 2 | Gear type / retract flag | ✅ Implemented | `sim/aircraft/gear/acf_gear_retract` |
| 0x0894, 0x092C, 0x09C4, 0x0A5C + | 2 | Engine combustion / N1 / N2 | ✅ Implemented | `sim/flightmodel/engine/ENGN_running`, `ENGN_N1_`, `ENGN_N2_` |
| 0x0AEC | 2 | Engine count | ✅ Implemented | `sim/aircraft/prop/acf_num_engines` |
| 0x0B74 / 0x0B7C / 0x0B94 | 4 | Fuel levels (centre/left/right) | ✅ Implemented | `sim/flightmodel/weight/m_fuel_total` (mirrored) |
| 0x0B78 / 0x0B80 / 0x0B98 | 4 | Fuel capacities (centre/left/right, gal) | ✅ Implemented | `sim/aircraft/weight/acf_m_fuel_tot` |
| 0x0AF4 | 2 | Fuel weight per gallon ×256 | ✅ Implemented | Fixed value 6.7 lb/gal |
| 0x3414 / 0x3415 | 1 | Seatbelt / No smoking | ✅ Implemented (Toliss/XCrafts/Zibo/FlyFactor) | `sim/cockpit2/switches/...`, `laminar/B738/...`, `AirbusFBW/...`, `XCrafts/ERJ/...`, Toliss `ckpt/oh/...`, FF `ff/seatsigns_on` |
| 0x0E90 / 0x0E92 / 0x0EF0 / 0x0EF2 | 2 | Surface wind speed/dir | ✅ Implemented | `sim/weather/wind_speed_kt`, `.../wind_direction_degt` |
| 0x030C | 4 | Landing rate (signed 256×m/s) | ✅ Implemented | Frozen on touchdown |
| 0x0304 / 0x0308 | 4 | Altitude (meters) / heading | ✅ Implemented | See snapshot |
| 0x3324 | 4 | Indicated altitude ft | ✅ Implemented | `sim/cockpit2/gauges/indicators/altitude_ft_pilot` (FSAirlines compat: pressure altitude) |
| 0x0330 / 0x0332 | 2 | QNH hPa / inHg | ✅ Implemented | `sim/cockpit2/gauges/actuators/barometer_setting_*` |
| 0x3542 / 0x3544 | 2 / 4 | Standby altimeter QNH / altitude | ✅ Implemented | Copilot baro/alt refs; fallback to main |
| 0x30C0 / 0x30C8 | 8 | Gross weight (lbs) / mass (slugs) | ✅ Implemented | `sim/flightmodel/weight/m_total` |
| 0x3BFC | 4 | Zero fuel weight (lbs×256) | ✅ Implemented | `sim/flightmodel/weight/m_total - m_fuel_total` |
| 0x1334 | 4 | Max gross weight (lbs×256) | ✅ Implemented | `sim/aircraft/weight/acf_m_max` |
| 0x11B8 / 0x11BA | 2 | G-Force | ✅ Implemented | `sim/flightmodel2/misc/gforce_normal` |
| 0x0354 | 2 | Transponder squawk | ✅ Implemented | `sim/cockpit2/radios/actuators/transponder_code` |
| 0x0B46 / 0x7B91 | 1 | Transponder mode | ✅ Implemented | `sim/cockpit2/radios/actuators/transponder_mode` |
| 0x0262 / 0x0264 | 2 | Pause control/indicator | ✅ Implemented | `sim/time/paused` |

## Open Offsets / TODO

| Offset | Size | Description | Status | Suggestion |
|--------|------|-------------|--------|-----------|
| 0x0778 / 0x078C / 0x0794 | 4 | Flaps/Spoilers/Strobes available | ⛔ Not available | No reliable X-Plane flags (no `has spoilers` dataref) |
| 0x02CC | 8 | Compass heading | ✅ Done | `sim/cockpit2/gauges/indicators/heading_electric_deg_mag_pilot` |
| 0x05DC | 2 | Slew mode indicator/control | ❌ Open | `sim/operation/override/override_slew` or custom flag |
| 0x084C | 2 | Crash/reset flag | ❌ Open | Test together with 0x0840 |
| 0x0F4Cff | 2 | Upper/mid/lower wind layers | ❌ Open | `sim/weather/wind_*` index 1–3 + altitude fields |
| 0x0300–0x0338 | … | Flight stage helpers | ❓ To clarify | Check whether APL2 reads these |
| 0x0898+ (Fuel flow, torque, oil) | 2/4 | Engine telemetry | Optional | `sim/flightmodel/engine/...` |
| 0x02FA / 0x07xx | 2/4 | Autopilot states | ❌ Open | `sim/cockpit/autopilot/...` |
| 0x0848ff | 2 | Landing lights availability etc. | ❓ | Check relevance |

Add rows as needed, especially when new offsets show up in the communication blocks.
