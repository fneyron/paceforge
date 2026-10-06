# Garmin Connect Developer Program — application draft (PaceForge)

Form: https://www.garmin.com/en-US/forms/GarminConnectDeveloperAccess/

**Company / developer:** PaceForge (paceforge.fr) — Florent Neyron
**Contact:** florent.neyron@gmail.com
**Product:** web application (no AI features) that helps trail and ultra runners prepare a race: passage-time plan per checkpoint, nutrition and drop bags, race-day pacing guidance, exported to the watch.

**APIs requested:** Health API (dailies, sleep, HRV, stress, body battery, user metrics incl. VO2max) and Activity API (activity summaries and details). Training API (push courses with planned passage times to the watch) if available.

**Use of the data:**
- Show the athlete's recovery and readiness before a race (HRV, resting HR, sleep, training load) on a "Santé" page.
- Calibrate the race plan on the athlete's own races (activity summaries: distance, elevation, moving time, laps).
- Data is shown only to the athlete who connected it; never sold or shared; deleted on disconnect or account deletion.

**Integration:** OAuth 2.0 (PKCE) consent from the athlete's Garmin account; Ping/Push notifications to https://paceforge.fr/garmin/webhook; deregistration and user-permission-change notifications honoured.

**Users:** existing PaceForge runners (Strava-connected); Garmin users currently can only sync activities through Strava and cannot share health metrics.

**Privacy policy:** https://paceforge.fr/privacy
