# Race dataset (pacing-model calibration)

Public race results used to calibrate and validate PaceForge's plan shape
(terrain, fatigue, stops). Collected slowly, stored once; never fetched at
runtime by the app. No names: bib, sex, category/age, rank and times only.

- `utmb/<event>_<year>/<race>.json.gz` — UTMB Live (utmblive-api.utmb.world):
  checkpoints, GPS track with LiveTrail terrain classes (R road, F track,
  T/T1/T2/T3 trail, S steps), finishers' passing times (in/out).
- `livetrail/<edition>/<course>.json.gz` — LiveTrail archives
  (livetrail.net/histo): checkpoints (km, D+, altitude, position, cutoff),
  finishers' arrival and departure times. No GPS track, no start clock.

Races of 40 km or more. Finishers per race: UTMB Live up to ~680 (most
races: the fastest third of the field whole, then an even sample by rank, or
all of them on smaller fields; the 47 races collected first, such as UTMB
Mont-Blanc 2022-2025 with its CCC and OCC, UTA, Translantau and Whistler,
still hold an even sample of 150-450 by rank), LiveTrail up to 80, sampled
evenly by rank.

## What is in it (committed, 2026-10-05)

| | stored | solo running races kept | finishers | checkpoint passages |
|---|---|---|---|---|
| UTMB Live | 175 races | 169 (124 with a GPS track) | 60 981 | 578 348 |
| LiveTrail | 821 courses | 601 | 44 762 | 341 138 |
| total | 996 | 770 races, 162 events (342 editions) | 105 743 | 919 486 |

Left out of every count and fit (`scripts/race_data/dataset_stats.py --list`
names each one):

- not a solo running race, by name / course / edition (`dataset.is_running`):
  mountain bike, cyclo, gravel, e-bike, triathlon / duathlon, relays (relais,
  relevos, staffel…), duos, couples and teams, walks, hikes and marches (Oxfam
  Trailwalker / OTW), stage races (MDS, Saharan, "2 days", "3 días"),
  time-limited formats (backyard, 3 h / 6 h), everesting, PTL (team, 300 km) —
  4 UTMB Live races, 212 LiveTrail courses;
- a median finisher effort speed (km + D+/100 per hour) above 12.5, which no
  running field reaches (bikes, broken timings): 3 LiveTrail courses;
- fewer than 10 finishers (5 LiveTrail courses) or no checkpoint distances
  (Whistler 2025, 2 races).

## What the model uses

- **Fit** (gradient curve, fatigue by hours, night, terrain): UTMB Live running
  races of 40-180 km WITH a GPS track — the gradient every 25 m needs the
  track. Held out BY EVENT, the test events fixed since the first fit
  (chiangmai, kullamannen, puertovallarta, transjeju, utmb, whistler; the
  Transjeju 2026 100M joined the test side). On 2026-10-04: 121 races
  (43 714 finishers; train 74 races / 29 596), test 47 races; the refit did
  not beat the app on the held-out events (18.59 vs 18.61 min mean checkpoint
  gap, 23/47 races, Transjeju guard worse) and was not adopted. Its selected
  form keeps the current curve as projected on the knot form: equal to the
  app's table from -15 to 25 %, not at -20..-16 % and 26..30 %. See
  `scripts/race_data/fit_results.txt`; a refit writes `refit_params.json`,
  and `fitted_params.json` (the fit the app's constants come from) changes
  only when a refit is adopted.
- **Extra validation**: the LiveTrail running courses of 40-180 km (595
  courses, 44 321 finishers), whose profile is rebuilt from each section's
  D+ / D- — never used to fit or to choose the model, night not scored (no
  start clock). The app: 18.58 min mean checkpoint gap (legacy 21.48).

Scripts: `scripts/race_data/collect_utmb.py`, `collect_livetrail.py`
(collection, resumable; `collect_utmb.py --retrack` fetches a track lost to a
failed download), `dataset.py` (one shape for both sources, running filter),
`dataset_stats.py` (the counts above), `fit_data.py` / `fit_model.py` (fit and
held-out validation), `validate_app.py` (the app's own path before / after a
refit). Check the sources' terms before any commercial use.
