# Cabin Sign DataRefs

| Aircraft / Add-on            | Seatbelt DataRef                                      | No-Smoking DataRef                                    | Type / Range            | Status & Notes |
|-----------------------------|-------------------------------------------------------|-------------------------------------------------------|-------------------------|----------------|
| X-Plane default             | `sim/cockpit2/annunciators/seatbelt_on`               | `sim/cockpit2/annunciators/smoking_on`                | `int` (0/1) annunciator | ✅ Confirmed; preferred source for offsets 0x3414/0x3415. |
| Generic fallback            | `sim/cockpit2/switches/fasten_seat_belts`             | `sim/cockpit2/switches/no_smoking`                    | `int` (0=Off,1=Auto,2=On) | ✅ Confirmed; used when annunciators are absent (older aircraft/plugins). |
| Zibo 737-800 (Laminar mod)  | `laminar/B738/toggle_seatbelt_sign`                   | `laminar/B738/toggle_smoking_sign`                    | `int` / bool (0=Off,1=On) | ✅ Polled before default Laminar refs. |
| Toliss Airbus (A319/A321/A346) | `toliss/apu/pedestal/seat_belts`                   | `toliss/apu/pedestal/no_smoking`                      | `int` (0=Off,1=Auto,2=On) | ✅ Mirrors ISCS pedestal selectors. |
| X-Crafts ERJ / E-Jets       | `XCrafts/ERJ/overhead/seat_belts`                     | `XCrafts/ERJ/overhead/no_smoking`                     | `int` (0=Off,1=Auto,2=On) | ✅ Matches overhead panel states. |
| FlightFactor A320/A350      | `ff/seatsigns_on`                                     | *(none exposed)*                                      | `int` / bool (0=Off,1=On) | ✅ Seatbelts wired; no published no-smoking dataref yet. |

*Legend:* ✅ ready to use; 🔄 requires in-sim validation; 🔍 pending identification. When adding a new aircraft mapping, prefer annunciator-style datarefs (actual lamp state). If only selector positions are exposed, map 0 → Off, 1 → Auto, 2 → On for FSUIPC compatibility.
