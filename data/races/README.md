# Race dataset (pacing-model calibration)

Public race results used to calibrate and validate PaceForge's plan shape
(terrain, fatigue, stops). Collected slowly, stored once; never fetched at
runtime by the app. No names: bib, sex, category/age, rank and times only.

- `utmb/<event>_<year>/<race>.json.gz` — UTMB Live (utmblive-api.utmb.world):
  checkpoints, GPS track with LiveTrail terrain classes (R road, F track,
  T/T1/T2/T3 trail, S steps), finishers' passing times (in/out).
- `livetrail/<edition>/<course>.json.gz` — LiveTrail archives
  (livetrail.net/histo): checkpoints (km, D+, altitude, position, cutoff),
  finishers' arrival and departure times.

Scripts: `scripts/race_data/collect_utmb.py`, `collect_livetrail.py`
(collection, resumable), `dataset.py` (one shape for both sources),
`calibrate.py` (scores the model's plan shape against every finisher).
Races over 40 km; up to 300 (UTMB) / 200 (LiveTrail) finishers per race,
sampled evenly by rank. Check the sources' terms before any commercial use.
