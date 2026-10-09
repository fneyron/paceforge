"""PaceForge's own nightly values, read back once per page (Santé, the race page).

One `Night` per wake-up day, from the rows the watches' syncs write
(health.py docstring): the main window and minutes asleep, the day's naps, the
24-h total (main night + naps), nightly HR, HRV and respiration, and the
context that keeps a night from firing the illness alert. Pure functions on
those rows; `load_nights` reads them.

Rules, with where they come from (evidence_final.md; (H) = a PaceForge
heuristic, never shown as a finding):
- Naps count in the 24-h total only, never in timing, regularity, nightly
  HR/HRV or the hypnogram (Mollicone 2008; Romyn 2018). A nap is stored under
  the day it ends, and counts once (day_naps, H; Craven 2022 counts sleep per
  24 h): a morning counts the day before's naps that ended in the 24 h before
  its wake (never that day's « rendormi » ones: part of its own night), its
  own naps that ended before its wake, and its own « rendormi » ones; an
  afternoon nap is the next morning's. Each day's bar, the Sommeil ring, the
  score, the < 6 h rule, the bands and the sleep owed read that one figure
  (day_tst24; owner's report 2026-10-09: an afternoon nap counted in two
  mornings' 24 h). A day with naps but no main episode has no 24-h total
  (« sieste seule, pas de nuit mesurée »): its bar shows its own naps, which
  the next morning counts.
- A « nap » that overlaps the main window or ends ≤ 30 min before it starts
  is the night's start (H, health.fold_naps; owner's report 2026-10-09:
  COROS's 23:03 → 00:09 « nap » of a 23:57 → 04:58 night showed as
  « + sieste » over it): folded into the night, when stored (COROS) and when read
  (build_nights: any watch's rows, the rows stored before): its start is the
  bedtime, its minutes asleep outside the window are added, never over the
  watch's own daily total. A nap starting after the wake stays a nap.
- Sleep stages (Night.stages, minutes of the main night: deep, light, rem,
  awake): the watch's estimate, shown, never judged (watches classify 50–70 %
  of the night correctly: de Zambotti 2024; deep or REM off by about an hour
  on one night: Chinoy 2021; no ideal amount: Ohayon 2017).
- Times are the watch's detection, approximate: printed rounded to 5 min
  (de Zambotti 2024).
- « Rendormi » (H): a nap starting ≤ 3 h after the main wake leaves that wake
  out of the wake median and spread, with no wake-shift word.
- « Après sieste tardive »: a nap ending after min(the usual bedtime − 7 h,
  16:00) on the evening before a night annotates it (H; Mograss 2022 for the
  7 h; Walsh 2021's 13:00–16:00 window); it does not exclude it and never
  judges the nap (Ohayon 2017).
- Every measured night counts (owner, 2026-10-08: « Tous les relevés VFC
  doivent compter en fait, pareil pour la FC », like WHOOP's and Oura's
  rolling baselines): in the bands, the 7-night means and the timing. The
  context tags only keep a night from firing the illness alert (a rise is
  expected there), and the nights' table names them (H): a session ≥ 90 min
  the day before (Myllymäki 2012), a vigorous session ending ≤ 2 h before
  sleep onset (« sortie intense le soir »: average HR ≥ 80 % of the
  heart-rate reserve, or ≥ 20 min above it in its laps or km splits; Stutz
  2019's ≤ 1 h and Myllymäki 2012's vigorous evening session, the 2 h and
  the 80 % are H: an easy evening run never mutes the alert), sleeping at
  altitude (Latshang 2013: ≥ 1 600 m where the athlete slept, H:
  night_altitude, for every user; carried to the next nights without any
  activity, 3 at most, H; a summit day from a valley is no night at
  altitude), a time zone change (Janse van Rensburg 2021; a step of more
  than 1 h, so a clock change at home is none; from the night it shows,
  ⌈1 night per zone⌉ eastwards, ⌈0.5⌉ westwards, « décalage horaire » from
  3 zones, H), the nights after an effort by its duration (H,
  sante_training.NIGHT_TAGS: night D+1 after a Longue; D+1 → D+3 after a
  Très longue, « après grosse sortie »: Hynynen 2010, nightly HR at 130 %
  after a marathon; D+1 → D+4 after an Ultra, « après ultra »: Fachan 2026,
  Kishi 2024; D is the day before the first morning after it, H:
  anchor_efforts), and the race page's « alcool hier » (Pietilä 2018). The
  nights of an alert episode are « FC de nuit haute » in the table (H; heart
  rate alone is never a diagnosis: evidence row 3). Santé (v4) reads nothing
  else: no planned race, no check-in (owner, 2026-10-08). The race page keeps
  the « alcool hier » and « malade » chips a check-in stored before v4 holds,
  and the nights after its race never fire the alert.
- COROS nightly HR on a day with a nap counts like any night, but never fires
  the illness alert until it is shown that COROS leaves the nap out of its
  « Sleep HR » line (docs/sante-v3-data-notes.md: consistent with the main
  sleep only).
- Bands (H): every measured night of 60 days on one watch (Dial 2025: brands
  average over different windows) — Santé's usual values (`normal`) the 60
  days up to and including last night, the illness alert the 60 days before
  its 2 nights; full from 14 nights, « provisoire » from 7 (owner decision
  2026-10-07), labelled so wherever it is read, until 14 — never for the
  illness alert nor its episodes (specific, not sensitive: Quer 2021; a
  provisional band would make the alert fire on noise). HR: median ± 3 bpm;
  HRV: exp(mean ln ± 0.5 SD) (the SWC convention of HRV-guided training);
  both SDs shrunk towards a prior (H, Kellmann 2018's Bayesian
  individualisation): σ0 = 0.10 ln units for HRV, 4 % of the median for the
  robust HR SD behind the alert line (Mishica 2022), as if 7 nights had it;
  24-h sleep: median ± 30 min; respiration: median.
- 7-night means: every measured night of the last 7, 3 at least (Plews 2014;
  Lau 2022).
- Illness alert (H): the full HR band of the watch that measured both nights
  (the 60 days before them), then 2 nights in a row each ≥ median + max(2
  robust SD, 5 bpm) (Alavi 2022: 2 nights at + 4 bpm over the median; ours
  stricter, specific, not sensitive: Quer 2021). Context-tagged nights (the
  nights after an effort included) never fire it; it re-arms by itself after
  them (Schwellnus 2016: symptoms more frequent in the 1–2 weeks after a
  race); on the race page, the nights after its race never fire it.
  Respiration ≥ median + 2/min only backs it up.
"""
import copy
import math
import statistics
from bisect import bisect_left
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services.health import HRV_METHOD, fold_naps, main_window, nap_windows

BAND_DAYS = 60  # (H)
MIN_BAND_NIGHTS = 14  # (H) a full normal
MIN_PROVISIONAL_NIGHTS = 7  # (H) a « provisoire » normal (owner decision 2026-10-07), until 14
MIN_MEAN_NIGHTS = 3  # of the last 7 (Plews 2014; Lau 2022)
WEEK_DAYS = 7
HR_BAND_BPM = 3  # (H)
HRV_BAND_SD = 0.5  # (H) the SWC convention of HRV-guided training
# the band's SD is shrunk towards a prior, √((n·s² + k·σ0²)/(n + k)): the Bayesian individualisation of reference
# ranges (Kellmann 2018), which matters most at 7–13 nights (an SD from 7 nights is 0.64–2.20 × the true one)
PRIOR_NIGHTS = 7  # (H) k: the prior weighs as much as 7 nights
HRV_SD_PRIOR = 0.10  # (H) σ0 in ln units (nocturnal RMSSD CV 9.5–12.4 %: Mishica 2022; ≈ 12 %: Buchheit 2014)
HR_SD_PRIOR = 0.04  # (H) σ0 as a share of the median (nocturnal HR CV ≈ 4.1 %: Mishica 2022)
SLEEP_BAND_MIN = 30  # (H)
ALERT_SD, ALERT_MIN_BPM = 2, 5  # (H)
# the breathing rate's usual line (owner, 2026-10-09: « utilise la respiration aussi si tu l'as »; research_ind_
# sleep_resp.md B3): its night-to-night SD is small (0.51 ± 0.20 /min: Miller 2020, 25,000 WHOOP members), so the
# line is the median + the larger of 1 /min and 2 SD, the SD shrunk towards 0.5 /min (H); one-sided, never a lower one
RESP_UP_MIN = 1.0  # (H) breaths/min
RESP_UP_SD = 2  # (H)
RESP_SD_PRIOR = 0.5  # (H) σ0, breaths/min (Miller 2020)
SHORT_DAY_MIN = 6 * 60  # ≤ 6 h per 24 h (Craven 2022)
REGULARITY_NIGHTS = 8  # of 28 days, any order (H; « 1 week or more » of the main sleep: ANSI/CTA/NSF-2052.1-A)
REGULAR_WINDOW = 60  # minutes: a bedtime « à moins d'1 h » of the usual one (the RU-SATED item: Ravyts 2021)
RESETTLE_H = 3  # (H) a nap starting this soon after the wake: « rendormi »
LATE_NAP_H = 7  # (H) a nap ending < 7 h before the usual bedtime (Mograss 2022, observational)
NAP_LATEST = time(16)  # (H) and never later than 16:00: the end of Walsh 2021's 13:00–16:00 nap window
USUAL_BED_NIGHTS, USUAL_BED_DAYS = 5, 28  # (H) the usual bedtime: the median of 28 days, 5 nights at least
LONG_SESSION_MIN = 90  # Myllymäki 2012
LATE_SESSION_GAP = timedelta(hours=2)  # (H) a vigorous session ending this soon before sleep onset (Stutz 2019: ≤ 1 h)
VIGOROUS_HRR = 0.8  # (H) average HR ≥ 80 % of the heart-rate reserve: vigorous (Myllymäki 2012); easy is ≈ 60–70 %
VIGOROUS_MIN = 20  # (H) or this many minutes above it in its laps, else its km splits (an interval session)
ALTITUDE_M = 1600  # (H) Latshang 2013 studied 1 630–2 590 m
ALTITUDE_CARRY = 3  # (H) nights an altitude night carries to while no activity says where the athlete is
TZ_CHANGE_MIN = 60  # (H) a step of MORE than this: a 1-h clock change (DST) at home is no time zone change
# nights per zone crossed, from the night the change shows: 1 eastwards, 0.5 westwards, rounded up, 1 at least
# (Janse van Rensburg 2021: natural alignment ≈ 1 day per zone east, 0.5 west; the same rate under 3 zones, H)
TZ_EAST, TZ_WEST = 1.0, 0.5
JETLAG_ZONES = 3  # « décalage horaire » from 3 zones (Janse van Rensburg 2021), « fuseau changé » under
TZ_LOOKBACK = 10  # days (H)
RACE_WINDOW = 7  # (H) the race page: the nights up to J+7 never fire the alert
ILL_TAIL = 2  # days after the last « malade » (H)
ILL_MERGE = 3  # « malade » days this close make one episode (H)
ILL_MAX = 14  # days an alert episode can run (H)
AXIS = (time(20, 0), time(12, 0))  # the timing chart's local axis
# whether COROS's « Sleep HR » line leaves a nap out: not shown (no HR curve), see the data notes
COROS_SLEEP_HR_NAP_VERIFIED = False

# context words, as the readouts print them (glyph ◇)
# « ill » is the athlete's own « malade » chip; « alert » an alert episode, never worded as a diagnosis (row 3)
TAG_WORDS = {"long": "après une sortie longue", "late": "sortie intense le soir", "altitude": "en altitude",
             "tz": "fuseau changé", "jetlag": "décalage horaire", "big": "après grosse sortie",
             "ultra": "après ultra", "alcohol": "alcool", "ill": "malade", "alert": "FC de nuit haute",
             "late_nap": "après sieste tardive"}
# the context nights: they count like any night (in the bands, the means, the timing), but never fire the alert
CONTEXT = ("long", "late", "altitude", "tz", "jetlag", "big", "ultra", "alcohol")


def tag_words(tags, words=None) -> list[str]:
    """A night's tags as the readouts print them, each word once (`words`, which replaces some of TAG_WORDS, may
    say several tags alike: Santé's table, sante_sleep.WORDS)."""
    w = {**TAG_WORDS, **(words or {})}
    return list(dict.fromkeys(w[t] for t in sorted(tags)))
_VALUE = {"hr": "hr", "hrv": "hrv", "resp": "resp"}
_SOURCE = {"hr": "hr_source", "hrv": "hrv_source", "tst24": "source", "resp": "resp_source"}


def round5(t: datetime) -> datetime:
    """To the nearest 5 min (the watch's times are approximate)."""
    m = round((t.hour * 60 + t.minute + t.second / 60) / 5) * 5
    return datetime.combine(t.date(), time(0)) + timedelta(minutes=m)


def clock_min(t: datetime) -> int:
    """Minutes after 18:00 of the evening before, so midnight never breaks a median."""
    return (t.hour * 60 + t.minute - 18 * 60) % 1440


def clock5(m: float) -> str:
    """Minutes after 18:00 → « 23:35 », rounded to 5 min."""
    t = (int(round(m / 5)) * 5 + 18 * 60) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


@dataclass
class Night:
    day: date  # the wake-up day
    source: str | None = None  # the watch of the main night
    start: datetime | None = None  # main window, local
    end: datetime | None = None
    asleep: int | None = None  # minutes asleep in the main episode
    naps: list = field(default_factory=list)  # [(start, end, minutes asleep)] ending on this day
    hr: float | None = None
    hr_min: float | None = None
    hr_method: str | None = None
    hr_nap_day: bool = False
    hr_source: str | None = None
    hrv: float | None = None
    hrv_n: int | None = None
    hrv_source: str | None = None
    resp: float | None = None
    resp_source: str | None = None
    tz: int | None = None  # minutes east of UTC
    timeline: bool = False  # real stage intervals stored (Garmin)
    stages: dict | None = None  # minutes of the main night per stage {deep, light, rem, awake}, the watch's estimate
    tags: set = field(default_factory=set)
    resettled: bool = False  # « rendormi »

    @property
    def nap_min(self) -> int:
        """Minutes asleep in the naps stored under this day (those that end on it); its 24 h: day_tst24."""
        return sum(m for _, _, m in self.naps)

    @property
    def bed5(self) -> datetime | None:
        return round5(self.start) if self.start else None

    @property
    def wake5(self) -> datetime | None:
        return round5(self.end) if self.end else None

    def value(self, metric: str):
        """hr, hrv or resp (read on every night of every band: no dict built per call); the 24-h sleep needs the
        day before: night_value. A measured value counts in the bands and the 7-night means, whatever the night's
        tags (owner, 2026-10-08)."""
        return getattr(self, _VALUE[metric])

    def source_of(self, metric: str) -> str | None:
        return getattr(self, _SOURCE[metric])

    def in_axis(self, a: datetime, b: datetime) -> bool:
        """A segment that fits the timing chart's 20:00 → 12:00 axis of this night."""
        lo = datetime.combine(self.day - timedelta(days=1), AXIS[0])
        return lo <= a and b <= datetime.combine(self.day, AXIS[1])


# ── building the nights ─────────────────────────────────────────────────────

STAGES = ("awake", "light", "deep", "rem")  # Éveil · Léger · Profond · Paradoxal (WHOOP's order)


def stages_of(value) -> dict | None:
    """{awake, light, deep, rem} minutes (ints) from a `sleep` row's
    details["stages"], or None when it is missing or odd (no sleep stage, a
    negative or implausible value). « awake » may be missing: 0."""
    if not isinstance(value, dict):
        return None
    out = {}
    for k in STAGES:
        v = value.get(k, 0 if k == "awake" else None)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 16 * 60:
            return None
        out[k] = int(round(v))
    return out if out["light"] + out["deep"] + out["rem"] > 0 else None


def _nap_parts(day: date, value: float, details: dict | None) -> list[tuple[datetime, datetime, int]]:
    """Each window with its share of the minutes asleep (in proportion to its length)."""
    wins = nap_windows(day, details)
    total = sum((b - a).total_seconds() for a, b in wins)
    if not wins or total <= 0:
        return [(None, None, round(value))] if value else []
    return [(a, b, round(value * (b - a).total_seconds() / total)) for a, b in wins]


def build_nights(rows: dict[str, dict[date, tuple]], today: date) -> dict[date, Night]:
    """{day: Night} from {metric: {day: (value, details, source)}} (metrics sleep,
    nap, hrv, hr_night, resp_night), days ≤ today. Rows of the watch's own HRV
    average (no PaceForge method) are left out."""
    out: dict[date, Night] = {}

    def night(d):
        return out.setdefault(d, Night(d))

    for d, (v, det, src) in rows.get("sleep", {}).items():
        win = main_window(d, det)
        if d > today or v is None or win is None:
            continue
        n = night(d)
        n.start, n.end, n.asleep, n.source = win[0], win[1], round(v), src
        n.tz, n.timeline = (det or {}).get("tz"), bool((det or {}).get("timeline"))
        n.stages = stages_of((det or {}).get("stages"))
    for d, (v, det, src) in rows.get("nap", {}).items():
        if d <= today and v:
            night(d).naps = [p for p in _nap_parts(d, v, det)]
    for d, (v, det, src) in rows.get("hrv", {}).items():
        if d <= today and (det or {}).get("method") == HRV_METHOD:
            n = night(d)
            n.hrv, n.hrv_n, n.hrv_source = v, (det or {}).get("n"), src
            if n.tz is None and (det or {}).get("tz") is not None:
                n.tz = det["tz"]
    for d, (v, det, src) in rows.get("hr_night", {}).items():
        if d <= today:
            n = night(d)
            n.hr, n.hr_min, n.hr_source = v, (det or {}).get("min"), src
            n.hr_method, n.hr_nap_day = (det or {}).get("method"), bool((det or {}).get("nap_day"))
    for d, (v, det, src) in rows.get("resp_night", {}).items():
        if d <= today:
            n = night(d)
            # Garmin's own night average (no readings of ours) makes its own band, never mixed with ours (H)
            summary = (det or {}).get("method") == "garmin_summary"
            n.resp, n.resp_source = v, f"{src} (résumé)" if summary else src
    # the « naps » that are a night's start (H, health.fold_naps), whichever watch stored them: folded into it
    for d in sorted(out):
        n = out[d]
        days = [x for x in (d - timedelta(days=1), d) if x in out]
        cands = [p for x in days for p in out[x].naps]
        if n.start is None or n.asleep is None or not cands:
            continue
        daily = (rows.get("sleep", {}).get(d, (None, None, None))[1] or {}).get("daily")
        daily = daily if isinstance(daily, (int, float)) and not isinstance(daily, bool) else None
        start, end, asleep, joined, _ = fold_naps(n.start, n.end, n.asleep, cands, daily, n.naps)
        if joined:
            n.start, n.end, n.asleep = start, end, asleep
            for x in days:
                out[x].naps = [p for p in out[x].naps if p not in joined]
    # « rendormi »: a nap starting ≤ 3 h after the main wake (H)
    for n in out.values():
        if n.end:
            n.resettled = any(a and n.end <= a <= n.end + timedelta(hours=RESETTLE_H) for a, _, _ in n.naps)
    return dict(sorted(out.items()))


# ── context tags ────────────────────────────────────────────────────────────

def _local(s, t: datetime) -> datetime:
    return (t + timedelta(seconds=s.offset or 0)).replace(tzinfo=None)


def vigorous(s, rest: float, peak: float) -> bool:
    """A vigorous session (H): its average HR ≥ 80 % of the heart-rate reserve,
    or ≥ 20 min above that in its laps (else its km splits: `s.segs`, loaded
    by load_segments for the sessions that could tag a night). « late » reads
    it; an easy evening run (≈ 60–70 %) is not one."""
    if not s.hr:
        return False
    hard = rest + VIGOROUS_HRR * (peak - rest)
    return s.hr >= hard or sum(m for m, hr in (getattr(s, "segs", None) or ()) if hr >= hard) >= VIGOROUS_MIN


def tag_nights(nights: dict[date, Night], sessions=(), feel: dict[date, dict] | None = None,
               rest: float = 50.0, peak: float = 190.0, day_alt: dict[date, float] | None = None) -> None:
    """Context tags on each night (see the module docstring), in place.
    `sessions`: sante_training.Session; `feel`: {day: feel_of(...)} (the race
    page's stored check-ins); `rest`/`peak`: the athlete's HR bounds;
    `day_alt`: {day: Garmin's average altitude that day} (day_altitudes)."""
    feel = feel or {}
    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    for d, n in nights.items():
        before = by_day.get(d - timedelta(days=1), [])
        if any(s.minutes >= LONG_SESSION_MIN for s in before):
            n.tags.add("long")
        if any(vigorous(s, rest, peak) for s in late_candidates(n, before + by_day.get(d, []))):
            n.tags.add("late")
        if (feel.get(d) or {}).get("alcohol"):
            n.tags.add("alcohol")
    _tag_altitude(nights, sessions, day_alt or {})
    _tag_timezones(nights, sessions)
    _tag_late_naps(nights)
    for d in illness_days(feel):
        if d in nights:
            nights[d].tags.add("ill")


def late_candidates(n: Night, sessions) -> list:
    """The sessions (of the day before and of the day) that tag night `n`
    « late » when they are vigorous: those ending ≤ 2 h before its sleep onset
    (H; Stutz 2019: ≤ 1 h). A night without a known onset has none."""
    if not n.start:
        return []
    out = []
    for s in sessions:
        end = _local(s, s.start) + timedelta(minutes=s.elapsed or s.minutes)
        if timedelta(0) <= n.start - end <= LATE_SESSION_GAP:
            out.append(s)
    return out


def _end(s) -> datetime:
    """A session's end on its local clock (naive), as late_candidates reads it."""
    return _local(s, s.start) + timedelta(minutes=s.elapsed or s.minutes)


def night_altitude(n: Night | None, d: date, by_end, day_alt: dict[date, float]) -> tuple[float | None, bool]:
    """Where the athlete slept the night that wakes on `d` (R2, H): (altitude in m or None, whether that day
    knew something: Garmin's altitude or an activity). First available of: (1) Garmin's average altitude of
    the day before (`day_alt`); (2) the ground altitude where the last outdoor activity that ended before
    sleep onset, the day before or that day (`by_end`: by the local day it ended; the day before only without
    a known onset), ended: looked up in the sync (Session.alt); (3) that activity's lowest point (Strava
    elev_low). An activity that cannot place the athlete (indoor, no position, a failed lookup) leaves the
    night untagged, and nothing carries across it."""
    if (a := day_alt.get(d - timedelta(days=1))) is not None:
        return a, True
    onset = n.start if n is not None else None
    days = (d - timedelta(days=1), d) if onset else (d - timedelta(days=1),)
    done = [s for x in days for s in by_end.get(x, ()) if onset is None or _end(s) <= onset]
    outdoor = [s for s in done if (s.located or s.elev_low is not None) and not s.indoor]
    if not outdoor:
        return None, bool(done)
    last = max(outdoor, key=_end)
    return (last.alt if last.alt is not None else last.elev_low), True


def _tag_altitude(nights: dict[date, Night], sessions, day_alt: dict[date, float]) -> None:
    """« en altitude » on each night slept at ≥ 1 600 m (night_altitude, H; Latshang 2013), carried to the
    following nights without any activity, 3 at most (H): a rest day up there is still a night up there. A
    summit reached from a valley is not one (the old highest-point rule): the end of the day says where the
    athlete slept."""
    if not nights:
        return
    by_end = defaultdict(list)
    for s in sessions:
        by_end[_end(s).date()].append(s)
    d, last, carry_until = min(nights), max(nights), None
    while d <= last:
        n = nights.get(d)
        alt, placed = night_altitude(n, d, by_end, day_alt)
        if alt is not None and alt >= ALTITUDE_M:
            carry_until = d + timedelta(days=ALTITUDE_CARRY)
            high = True
        elif placed:  # lower down, or somewhere unknown (indoors, a failed lookup): no carry across it
            carry_until, high = None, False
        else:
            high = carry_until is not None and d <= carry_until
        if high and n is not None:
            n.tags.add("altitude")
        d += timedelta(days=1)


def hr_segments(laps, splits) -> tuple:
    """((minutes, average HR), …) of an activity's laps, else of its km splits
    (Strava's shapes): what « ≥ 20 min above » and the Entraînement dial's
    load (sante_training.session_load) read; () without them."""
    for rows in (laps, splits):
        if not isinstance(rows, list):
            continue
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                hr = float(r.get("average_heartrate") or 0)
                sec = float(r.get("moving_time") or r.get("elapsed_time") or 0)
            except (TypeError, ValueError):
                continue
            if hr > 0 and sec > 0:
                out.append((sec / 60, hr))
        if out:
            return tuple(out)
    return ()


async def read_segments(db: AsyncSession, sessions) -> None:
    """Each of `sessions` with an average HR and no `segs` yet gets its laps'
    (else its splits') HR segments (hr_segments), in one read; () without
    them. The laps are Strava's, or COROS's auto laps stored in Strava's
    shape (coros.apply_details)."""
    from app.models.activity import Activity

    want = {}
    for s in sessions:
        if s.hr and getattr(s, "segs", None) is None:
            want.setdefault(s.id, []).append(s)
    if not want:
        return
    rows = (await db.execute(select(Activity.id, Activity.laps, Activity.splits_metric)
                             .where(Activity.id.in_(list(want))))).all()
    found = {i: hr_segments(laps, splits) for i, laps, splits in rows}
    for i, ss in want.items():
        for s in ss:
            s.segs = found.get(i, ())


async def load_segments(db: AsyncSession, nights: dict[date, Night], sessions) -> None:
    """Each session that could tag a night « late » (late_candidates) gets its
    laps' (else its splits') HR segments as `segs` (read_segments); the others
    keep None (their average alone is read)."""
    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    await read_segments(db, [s for d, n in nights.items()
                             for s in late_candidates(n, by_day.get(d - timedelta(days=1), []) + by_day.get(d, []))])


def tz_nights(step: float) -> tuple[str, int]:
    """(tag, nights) of a UTC-offset step of `step` minutes (east > 0), the
    shorter way round: « décalage horaire » from 3 zones, else « fuseau
    changé », for ⌈1 × zones⌉ nights eastwards, ⌈0.5 × zones⌉ westwards, 1 at
    least (Janse van Rensburg 2021; the same rate under 3 zones, H)."""
    step = (step + 720) % 1440 - 720  # −12 h → +12 h
    zones = abs(step) / 60
    rate = TZ_EAST if step > 0 else TZ_WEST
    return ("jetlag" if zones >= JETLAG_ZONES else "tz"), max(1, math.ceil(rate * zones - 1e-9))


def _tag_timezones(nights: dict[date, Night], sessions) -> None:
    """« fuseau changé » / « décalage horaire » (tz_nights) on the night a new
    UTC offset shows (the night's own, else that of the sessions of the day
    before or that day) and the nights after it, by the zones crossed and the
    direction (Janse van Rensburg 2021)."""
    offset = {}
    for s in sorted(sessions, key=lambda s: s.start):
        if s.offset is not None:  # a session without a known offset says nothing about the time zone
            offset[s.day] = round(s.offset / 60)
    track = {}
    for d in sorted(set(nights) | set(offset)):
        n = nights.get(d)
        tz = n.tz if n and n.tz is not None else offset.get(d, offset.get(d - timedelta(days=1)))
        if tz is not None:
            track[d] = tz
    days = sorted(track)
    for i, d in enumerate(days):
        prev = next((days[j] for j in range(i - 1, -1, -1) if (d - days[j]).days <= TZ_LOOKBACK), None)
        if prev is not None and abs((track[d] - track[prev] + 720) % 1440 - 720) > TZ_CHANGE_MIN:
            tag, count = tz_nights(track[d] - track[prev])
            for k in range(count):
                if d + timedelta(days=k) in nights:
                    nights[d + timedelta(days=k)].tags.add(tag)


def _onsets(nights: dict[date, Night]) -> tuple[list[date], list[int]]:
    """The main nights' days and onsets (minutes after 18:00), by day: every night counts (as in timing)."""
    pairs = sorted((x, clock_min(m.start)) for x, m in nights.items() if m.start)
    return [x for x, _ in pairs], [v for _, v in pairs]


def nap_cutoff(nights: dict[date, Night], d: date, onsets=None) -> datetime | None:
    """When a nap becomes « tardive » for the night that wakes on `d`: on the
    evening before, min(the usual bedtime − 7 h, 16:00) (H; Mograss 2022 for
    the 7 h, Walsh 2021's 13:00–16:00 window); the usual bedtime is the median
    onset of the 28 days before (5 nights at least, every night counted), else
    this night's own onset. None without an onset. `onsets`: _onsets(nights),
    when read for many nights."""
    n = nights.get(d)
    if n is None or n.start is None:
        return None
    days, mins = onsets or _onsets(nights)
    beds = mins[bisect_left(days, d - timedelta(days=USUAL_BED_DAYS)):bisect_left(days, d)]
    bed = statistics.median(beds) if len(beds) >= USUAL_BED_NIGHTS else clock_min(n.start)
    evening = datetime.combine(d - timedelta(days=1), time(0))
    # clock_min counts from 18:00 of the evening before: 18 h after its midnight
    at = min(bed + 18 * 60 - LATE_NAP_H * 60, NAP_LATEST.hour * 60 + NAP_LATEST.minute)
    return evening + timedelta(minutes=at)


def _tag_late_naps(nights: dict[date, Night]) -> None:
    """« après sieste tardive » on a night after a nap that ended past its
    cut-off (nap_cutoff) and before its onset: an annotation, never judged
    (Ohayon 2017: no consensus on naps as a mark of good sleep), a word in the
    nights' table."""
    onsets = None
    for d, n in nights.items():
        ends = [b for m in (nights.get(d - timedelta(days=1)), n) if m for _, b, _ in m.naps
                if b and n.start and b <= n.start]
        if not ends:
            continue
        onsets = onsets or _onsets(nights)
        if (cut := nap_cutoff(nights, d, onsets)) is not None and any(cut <= b for b in ends):
            n.tags.add("late_nap")


def anchor_efforts(nights: dict[date, Night], efforts=()) -> list:
    """The efforts with D moved to the day before when the athlete slept after
    one and woke that same day (an ultra finished at 06:30, then asleep 07:00
    → 13:00: that sleep is its first night after, its morning the first in its
    window). sante_training.effort_day already does it for a finish before
    06:00 (H); this reads the nights for a later dawn finish."""
    from dataclasses import replace

    out = []
    for e in efforts:
        d0 = e.end.date()
        n = nights.get(d0)
        if e.day == d0 and n is not None and n.start is not None and n.start >= e.end:
            e = replace(e, day=d0 - timedelta(days=1))
        out.append(e)
    return out


def tag_efforts(nights: dict[date, Night], efforts=()) -> None:
    """The nights after an effort (sante_training.Effort, anchored:
    anchor_efforts), by the class of its nights (its duration whatever the
    sport, sante_training.NIGHT_TAGS), in place: a Longue « après une sortie
    longue » on D+1 (every Longue, the D+ rule included, whatever the 90-min
    rule saw), a Très longue « après grosse sortie » D+1 → D+3, an Ultra
    « après ultra » D+1 → D+4: they never fire the illness alert, and count
    in the bands and the means like any night. D+1 only when that sleep
    started after the effort ended (the sleep before a night start is not
    after it). Drawn like any night; counted in the 24-h totals (H)."""
    from app.services.sante_training import NIGHT_TAGS

    for e in efforts:
        if e.nights is None:
            continue
        tag, last = NIGHT_TAGS[e.nights]
        for k in range(1, last + 1):
            n = nights.get(e.day + timedelta(days=k))
            if n is not None and (k > 1 or n.start is None or n.start >= e.end):
                n.tags.add(tag)


def illness_days(feel: dict[date, dict]) -> set[date]:
    """The days of a « malade » episode (H): « malade » days within 3 days of
    each other make one episode, which runs 2 days after the last. (The nights
    of an alert episode are alert_episodes', tagged « alert »: heart rate alone
    never says « malade ».)"""
    sick = sorted(d for d, f in (feel or {}).items() if f and "sick" in (f.get("why") or []))
    out = set()
    i = 0
    while i < len(sick):
        j = i
        while j + 1 < len(sick) and (sick[j + 1] - sick[j]).days <= ILL_MERGE:
            j += 1
        d = sick[i]
        while d <= sick[j] + timedelta(days=ILL_TAIL):
            out.add(d)
            d += timedelta(days=1)
        i = j + 1
    return out


# ── the athlete's normal ────────────────────────────────────────────────────

def _latest_source(nights: dict[date, Night], metric: str, until: date) -> str | None:
    kept = _kept(nights)
    if kept is None:
        return _latest_source_(nights, metric, until)
    key = ("source", metric, until)
    if key not in kept:
        kept[key] = _latest_source_(nights, metric, until)
    return kept[key]


def _latest_source_(nights: dict[date, Night], metric: str, until: date) -> str | None:
    for k in range(WEEK_DAYS):  # nearly always a night of the last days: no scan of the whole year
        x = until - timedelta(days=k)
        if night_value(nights, x, metric) is not None:
            return nights[x].source_of(metric)
    lo = until - timedelta(days=WEEK_DAYS)
    last = max((d for d in nights if d <= lo and night_value(nights, d, metric) is not None), default=None)
    return nights[last].source_of(metric) if last is not None else None


# ── a memo for nights that no longer change (the score's 14-day history) ────

_MEMO: ContextVar[dict | None] = ContextVar("nights_memo", default=None)


@contextmanager
def memo():
    """A scope in which band(), mean7() and illness_alert() on the nights dicts passed
    to freeze() are computed once per arguments (Santé reads the same bands and
    alerts again for each day of the score's history)."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


def freeze(nights: dict[date, Night]) -> dict[date, Night]:
    """Inside memo(): `nights` (its nights and their tags) will not change any
    more, so its bands and alerts can be kept. A no-op outside memo()."""
    m = _MEMO.get()
    if m is not None and id(nights) not in m:
        m[id(nights)] = (nights, {})  # the dict is held: its id cannot be reused while the scope lives
    return nights


def _kept(nights) -> dict | None:
    m = _MEMO.get()
    hit = m.get(id(nights)) if m else None
    return hit[1] if hit and hit[0] is nights else None


def retagged(n: Night, tags=()) -> Night:
    """A copy of a night with these tags (the others are not copied: a copy is never mutated but its tags)."""
    out = copy.copy(n)
    out.tags = set(tags)
    return out


def shrunk_sd(s: float, n: int, prior: float) -> float:
    """An SD of `n` nights shrunk towards `prior`: √((n·s² + k·σ0²)/(n + k)), k = 7 (H)."""
    return math.sqrt((n * s * s + PRIOR_NIGHTS * prior * prior) / (n + PRIOR_NIGHTS))


def band(nights: dict[date, Night], metric: str, until: date, source: str | None = None,
         full: bool = False) -> dict | None:
    kept = _kept(nights)
    if kept is None:
        return _band(nights, metric, until, source, full)
    key = ("band", metric, until, source, full)
    if key not in kept:
        kept[key] = _band(nights, metric, until, source, full)
    return kept[key]


def _band(nights: dict[date, Night], metric: str, until: date, source: str | None = None,
          full: bool = False) -> dict | None:
    """The athlete's normal for `metric` (hr, hrv, tst24, resp) from every
    measured night of the 60 days before `until` (excluded), whatever its tags
    (owner, 2026-10-08), on one watch (the latest one's unless `source`): None
    under 7 nights, `provisional` from 7 to 13 nights (« provisoire », H);
    `full`: None under 14 nights (the illness alert and its episodes)."""
    source = source or _latest_source(nights, metric, until - timedelta(days=1))
    lo = until - timedelta(days=BAND_DAYS)
    vals = [v for d, n in nights.items()
            if lo <= d < until and (v := night_value(nights, d, metric)) is not None and n.source_of(metric) == source]
    out = {"metric": metric, "n": len(vals), "source": source, "until": until,
           "provisional": len(vals) < MIN_BAND_NIGHTS}
    if len(vals) < (MIN_BAND_NIGHTS if full else MIN_PROVISIONAL_NIGHTS):
        return None
    if metric == "hrv":
        logs = [math.log(v) for v in vals if v > 0]
        mean, sd = statistics.fmean(logs), shrunk_sd(statistics.stdev(logs), len(logs), HRV_SD_PRIOR)
        out.update(center=math.exp(mean), lo=math.exp(mean - HRV_BAND_SD * sd), hi=math.exp(mean + HRV_BAND_SD * sd),
                   sd=sd)
        return out
    med = statistics.median(vals)
    sd = 1.4826 * statistics.median(abs(v - med) for v in vals)  # robust
    if metric == "hr":  # the SD behind the alert line, shrunk towards 4 % of the median (H)
        sd = shrunk_sd(sd, len(vals), HR_SD_PRIOR * med)
    out.update(center=med, sd=sd)
    if metric == "hr":
        out.update(lo=med - HR_BAND_BPM, hi=med + HR_BAND_BPM, alert=med + max(ALERT_SD * sd, ALERT_MIN_BPM))
    elif metric == "tst24":
        out.update(lo=med - SLEEP_BAND_MIN, hi=med + SLEEP_BAND_MIN)
    else:  # resp: no band drawn, one upper line (RESP_UP_MIN, RESP_UP_SD)
        sd = shrunk_sd(sd, len(vals), RESP_SD_PRIOR)
        out.update(sd=sd, lo=None, hi=None, up=med + max(RESP_UP_MIN, RESP_UP_SD * sd))
    return out


def normal(nights: dict[date, Night], metric: str, d: date) -> dict | None:
    """The usual values day `d` reads (Santé's cards, their status lines and the score): the band of every
    measured night of the 60 days up to and including `d` — last night included (owner, 2026-10-08: « Tous les
    relevés VFC doivent compter en fait, pareil pour la FC », like WHOOP's and Oura's rolling baselines) — on the
    watch its 7-night mean reads (mean_source)."""
    return band(nights, metric, d + timedelta(days=1), source=mean_source(nights, metric, d))


def mean_source(nights: dict[date, Night], metric: str, today: date) -> str | None:
    """The watch the 7-night mean of `today` reads (the latest one that measured
    `metric`): its value is judged against that watch's band only (Dial 2025)."""
    return _latest_source(nights, metric, today)


def mean7(nights: dict[date, Night], metric: str, today: date) -> dict | None:
    kept = _kept(nights)
    if kept is None:
        return _mean7(nights, metric, today)
    key = ("mean7", metric, today)
    if key not in kept:
        kept[key] = _mean7(nights, metric, today)
    return kept[key]


def _mean7(nights: dict[date, Night], metric: str, today: date) -> dict | None:
    """The mean of every measured night of the last 7 days on the latest watch (HRV: exp of the mean ln), {value,
    n}; None under 3 nights."""
    source = _latest_source(nights, metric, today)
    lo = today - timedelta(days=6)
    vals = [v for d, n in nights.items()
            if lo <= d <= today and (v := night_value(nights, d, metric)) is not None and n.source_of(metric) == source]
    if len(vals) < MIN_MEAN_NIGHTS:
        return None
    v = math.exp(statistics.fmean(math.log(x) for x in vals)) if metric == "hrv" else statistics.fmean(vals)
    return {"value": v, "n": len(vals)}


def status(value: float | None, b: dict | None) -> str | None:
    """« above » / « below » / « in » against the band; None without both."""
    if value is None or not b or b.get("lo") is None:
        return None
    return "above" if value > b["hi"] else "below" if value < b["lo"] else "in"


# ── the 24 hours before the morning's wake ──────────────────────────────────

def day_naps(nights: dict[date, Night], d: date) -> list[tuple[datetime | None, datetime | None, int]]:
    """The naps the morning of `d` counts in its 24 h (day_tst24), each nap once
    across the days (H; Craven 2022 counts sleep per 24 h; owner's report
    2026-10-09: an afternoon nap was in two mornings' 24 h): those of the day
    before that ended within the 24 h before the main wake, only their minutes
    inside those 24 h, never its « rendormi » ones (≤ 3 h after its own wake:
    part of its night, counted there); and those of `d` that ended before its
    wake, its own « rendormi » ones, or carry no times. A later nap of `d` (an
    afternoon nap) is the next morning's. [] without a main night."""
    n = nights.get(d)
    if n is None or n.asleep is None or n.end is None:
        return []
    out = [(a, b, m) for a, b, m in n.naps
           if a is None or b is None or b <= n.end or n.end <= a <= n.end + timedelta(hours=RESETTLE_H)]
    prev = nights.get(d - timedelta(days=1))
    lo = n.end - timedelta(hours=24)
    for a, b, m in (prev.naps if prev else []):
        if a is None or b is None or b <= lo or (n.start is not None and b > n.start):
            continue
        if prev.end is not None and prev.end <= a <= prev.end + timedelta(hours=RESETTLE_H):
            continue  # « rendormi »: the night before's, already counted there
        if a < lo:
            m = round(m * (b - lo).total_seconds() / (b - a).total_seconds())
        if m > 0:
            out.append((a, b, m))
    return sorted(out, key=lambda x: x[0] or datetime.min)


def day_tst24(nights: dict[date, Night], d: date) -> int | None:
    """Minutes asleep in the 24 h before the main wake of `d`: the main night
    and the naps day_naps counts, each nap once across the days. None without
    a main night that morning (a nap alone has no 24-h total). The figure of
    the Sommeil ring, the score, each day's bar, the tst24 bands and the
    sleep owed (slept_before_wake)."""
    n = nights.get(d)
    if n is None or n.asleep is None:
        return None
    return n.asleep + sum(m for _, _, m in day_naps(nights, d))


def bar_nap_min(nights: dict[date, Night], d: date) -> int:
    """The nap minutes day `d`'s bar stacks over its night (Santé's, the race page's): those its 24 h counts
    (day_naps: each nap on one bar only); a day without a main night, its own naps (« sieste seule », no 24-h
    total: the next morning counts them)."""
    n = nights.get(d)
    if n is None:
        return 0
    return n.nap_min if n.asleep is None else sum(m for _, _, m in day_naps(nights, d))


def slept_before_wake(nights: dict[date, Night], d: date) -> int | None:
    """The sleep of the morning of `d` the sleep owed reads (sante_sleep.sleep_need): day_tst24's figure, each
    nap counted once across the days, never repaid twice nor lost. None without a main night."""
    return day_tst24(nights, d)


def night_value(nights: dict[date, Night], d: date, metric: str):
    """The value of `metric` (hr, hrv, resp: the night's own; tst24: day_tst24, which reads the day before) of day
    `d`, None when not measured: what the bands and the 7-night means read."""
    if metric == "tst24":
        return day_tst24(nights, d)
    n = nights.get(d)
    return None if n is None else n.value(metric)


# ── timing ──────────────────────────────────────────────────────────────────

def timing(nights: dict[date, Night], today: date, days: int = 28) -> dict:
    """Median onset and wake (minutes after 18:00, local clock) over the main
    nights of the last `days` days, and once 8 nights are there (H) their
    spread (SD of the onset, the regularity measure of ANSI/CTA/NSF-2052.1-A)
    and `bed_near`, the nights whose onset is within 1 h of the median (the
    RU-SATED window: Ravyts 2021), what the page shows. Every night counts,
    whatever its tags (owner, 2026-10-08); a « rendormi » wake counts in
    neither the wake median nor its spread (H)."""
    ns = [n for d, n in nights.items() if today - timedelta(days=days - 1) <= d <= today and n.start]
    beds = [clock_min(n.start) for n in ns]
    wakes = [clock_min(n.end) for n in ns if not n.resettled]
    out = {"n": len(ns), "bed": statistics.median(beds) if beds else None,
           "wake": statistics.median(wakes) if wakes else None, "bed_sd": None, "wake_sd": None,
           "regular_ok": len(ns) >= REGULARITY_NIGHTS, "bed_near": None}
    if out["regular_ok"]:
        out["bed_sd"] = statistics.stdev(beds)  # the standard's measure (ANSI/CTA/NSF-2052.1-A)
        out["wake_sd"] = statistics.stdev(wakes) if len(wakes) >= REGULARITY_NIGHTS else None
        out["bed_near"] = sum(1 for b in beds if abs(b - out["bed"]) <= REGULAR_WINDOW)  # what the page shows
    return out


# ── illness alert ───────────────────────────────────────────────────────────

def alert_night(nights: dict[date, Night], races, d: date) -> bool:
    """A night that may fire the alert: measured HR, no context tag (a rise is
    expected after an ultra or a long-haul flight), not a COROS nap day (its
    « Sleep HR » unverified), not after a race (`races`: [(day, name)], the
    race page's Routes and sessions marked as races)."""
    n = nights.get(d)
    if not n or n.hr is None or not n.tags.isdisjoint(CONTEXT):
        return False
    if n.hr_nap_day and n.hr_method == "coros_sleep_summary" and not COROS_SLEEP_HR_NAP_VERIFIED:
        return False
    return not any(0 <= (d - rd).days <= RACE_WINDOW for rd, _ in races)  # race week may fire it, not after


def illness_alert(nights: dict[date, Night], today: date, races=()) -> dict | None:
    kept = _kept(nights)
    if kept is None:
        return _illness_alert(nights, today, races)
    # the races it reads: those of the 8 days up to today (« not after a race », alert_night)
    key = ("alert", today, tuple(sorted(rd for rd, _ in races if 0 <= (today - rd).days <= RACE_WINDOW + 1)))
    if key not in kept:
        kept[key] = _illness_alert(nights, today, races)
    return kept[key]


def resp_up_nights(nights: dict[date, Night], today: date) -> dict | None:
    """The breathing rate over its usual line 2 nights in a row (yesterday's and today's), each against the FULL
    band (14 nights) of the 60 days before the first one, on the watch that measured both, neither night tagged
    (CONTEXT: after an effort, altitude, a time zone, an evening session, alcohol: a faster breathing expected
    there): {days, values, line}; None otherwise. The score is then capped (sante_score.CAP_RESP), as WHOOP's one-
    sided adjustment does (« only when respiratory rate is elevated »); never an illness sentence on its own (about
    5 % of healthy days flagged: Miller 2020)."""
    d2, d1 = today, today - timedelta(days=1)
    n1, n2 = nights.get(d1), nights.get(d2)
    if not (n1 and n2) or n1.resp is None or n2.resp is None or n1.resp_source != n2.resp_source:
        return None
    if not (n1.tags.isdisjoint(CONTEXT) and n2.tags.isdisjoint(CONTEXT)):
        return None
    b = band(nights, "resp", d1, source=n1.resp_source, full=True)
    if not b or not (n1.resp >= b["up"] and n2.resp >= b["up"]):
        return None
    return {"days": [d1, d2], "values": [n1.resp, n2.resp], "line": b["up"]}


def _illness_alert(nights: dict[date, Night], today: date, races=()) -> dict | None:
    """The two nights in a row (yesterday's and today's) each ≥ the HR band's
    alert line, against the FULL band (14 nights, never a provisional one) of
    the 60 days before them on the watch that measured both (one band per
    watch: a new watch has none for 14 nights, so no alert): {days, values, threshold, resp_up, source}; None otherwise (no
    band, a night missing or tagged, two watches)."""
    d2, d1 = today, today - timedelta(days=1)
    if not all(alert_night(nights, races, d) for d in (d1, d2)):
        return None
    src = nights[d2].hr_source
    if nights[d1].hr_source != src:
        return None
    b = band(nights, "hr", d1, source=src, full=True)  # never a provisional band: specific, not sensitive (Quer 2021)
    if not b:
        return None
    vals = [nights[d1].hr, nights[d2].hr]
    if not all(v >= b["alert"] for v in vals):
        return None
    rb = band(nights, "resp", d1, full=True)
    resp = [nights[d].resp for d in (d1, d2)]
    resp_up = (all(r is not None and r >= rb["up"] for r in resp)) if rb else None
    return {"days": [d1, d2], "values": vals, "threshold": b["alert"], "resp_up": resp_up, "source": src}


def alert_episodes(nights: dict[date, Night], today: date, races=(), days: int = BAND_DAYS + 7) -> set[date]:
    """The nights of the alert episodes of the last `days` days (H): from the
    first of the 2 nights, while the measured nights stay above the band (14
    days at most)."""
    out: set[date] = set()
    d = today - timedelta(days=days)
    while d <= today:
        a = illness_alert(nights, d, races)
        if a and a["days"][0] not in out:
            b = band(nights, "hr", a["days"][0], source=a["source"], full=True)
            k = a["days"][0]
            while k <= min(today, a["days"][0] + timedelta(days=ILL_MAX)):
                n = nights.get(k)
                if n and n.hr is not None and n.hr <= b["hi"] and k > a["days"][1]:
                    break
                out.add(k)
                k += timedelta(days=1)
        d += timedelta(days=1)
    return out


# ── reading ─────────────────────────────────────────────────────────────────

NIGHT_ROWS = ("sleep", "nap", "hrv", "hr_night", "resp_night", "feel")


async def read_rows(db: AsyncSession, user_id: int, lo: date, hi: date,
                    metrics: tuple[str, ...] = NIGHT_ROWS) -> dict[str, dict[date, tuple]]:
    """{metric: {day: (value, details, source)}} of the nightly rows (and the
    check-ins, unless `metrics` leaves them out: Santé v4 never reads them)."""
    rows = await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.details,
               HealthMetric.source)
        .where(HealthMetric.user_id == user_id, HealthMetric.metric.in_(metrics),
               HealthMetric.date >= lo, HealthMetric.date <= hi))
    out: dict[str, dict[date, tuple]] = defaultdict(dict)
    for metric, d, v, det, src in rows.all():
        out[metric][d] = (v, det or {}, src)
    return out


def rest_hr(nights: dict[date, Night], today: date) -> float:
    """The athlete's resting HR for the « vigorous » test: the median nightly
    HR of the last 60 days (3 nights at least), else 50."""
    vals = [n.hr for d, n in nights.items() if today - timedelta(days=BAND_DAYS) < d <= today and n.hr
            and 25 <= n.hr <= 100]
    return statistics.median(vals) if len(vals) >= 3 else 50.0


def tag_alerts(nights: dict[date, Night], today: date, races=()) -> None:
    """« FC de nuit haute » on the nights of the alert episodes (alert_episodes),
    in place: a word in the nights' table, never worded as « malade »."""
    for d in alert_episodes(nights, today, races):
        if d in nights:
            nights[d].tags.add("alert")


SANTE_ROWS = ("sleep", "nap", "hrv", "hr_night", "resp_night")  # Santé v4: no check-in


def tag_activities(nights: dict[date, Night], sessions=(), efforts=(), rest: float = 50.0,
                   peak: float = 190.0, day_alt: dict[date, float] | None = None) -> None:
    """Santé v4's tags, from the activities alone, in place: the context of
    each night (sortie longue la veille, sortie intense le soir, altitude
    with Garmin's day altitudes `day_alt`, fuseau) and « après grosse
    sortie » (`efforts`: sante_training.efforts, anchored on these nights).
    No planned race, no check-in; « FC de nuit haute » comes after
    (tag_alerts)."""
    tag_nights(nights, sessions, {}, rest, peak, day_alt)
    tag_efforts(nights, anchor_efforts(nights, efforts))


async def day_altitudes(db: AsyncSession, user_id: int, lo: date, hi: date) -> dict[date, float]:
    """{day: Garmin's average altitude where the watch was worn} (its steps row, garmin.parse_summary): the
    night after each day is placed by it first (night_altitude)."""
    alt = HealthMetric.details["alt"].as_float()
    rows = await db.execute(select(HealthMetric.date, alt).where(
        HealthMetric.user_id == user_id, HealthMetric.metric == "steps", HealthMetric.source == "Garmin",
        HealthMetric.date >= lo, HealthMetric.date <= hi, alt.is_not(None)))
    return {d: float(v) for d, v in rows.all()}


async def load_nights(db: AsyncSession, user_id: int, today: date, days: int = 400, sessions=(), efforts=(),
                      rest: float | None = None, peak: float = 190.0,
                      day_alt: dict[date, float] | None = None) -> dict[date, Night]:
    """Santé's nights of the last `days` days, tagged from the activities
    and Garmin's day altitudes (`day_alt`, else day_altitudes: tag_activities),
    then « FC de nuit haute » (the alert episodes). `rest` None: from the
    nights (rest_hr)."""
    lo = today - timedelta(days=days)
    rows = await read_rows(db, user_id, lo, today, SANTE_ROWS)
    nights = build_nights(rows, today)
    if rest is None:
        rest = rest_hr(nights, today)
    if day_alt is None:
        day_alt = await day_altitudes(db, user_id, lo, today)
    await load_segments(db, nights, sessions)
    tag_activities(nights, sessions, efforts, rest, peak, day_alt)
    tag_alerts(nights, today)
    return nights
