"""PaceForge's own nightly values, read back once per page (Santé, the race page).

One `Night` per wake-up day, from the rows the watches' syncs write
(health.py docstring): the main window and minutes asleep, the day's naps, the
24-h total (main night + naps), nightly HR, HRV and respiration, and the
context that keeps a night out of the athlete's normal. Pure functions on
those rows; `load_nights` reads them.

Rules, with where they come from (evidence_final.md; (H) = a PaceForge
heuristic, never shown as a finding):
- Naps count in the 24-h total only, never in timing, regularity, nightly
  HR/HRV or the hypnogram (Mollicone 2008; Romyn 2018). A nap belongs to the
  day it ends. A day with naps but no main episode has no 24-h total (« sieste
  seule, pas de nuit mesurée »).
- Times are the watch's detection, approximate: printed rounded to 5 min
  (de Zambotti 2024).
- « Rendormi » (H): a nap starting ≤ 3 h after the main wake leaves that wake
  out of the wake median and spread, with no wake-shift word.
- « Après sieste tardive »: a nap ending < 7 h before the next main onset
  annotates that night (Mograss 2022); it does not exclude it.
- Excluded nights (H), out of the band and of the 7-night means: a session
  ≥ 90 min the day before (Myllymäki 2012), a vigorous session ending ≤ 2 h
  before sleep onset (« sortie intense le soir »: average HR ≥ 80 % of the
  heart-rate reserve, or ≥ 20 min above it in its laps or km splits; Stutz
  2019's ≤ 1 h and Myllymäki 2012's vigorous evening session, the 2 h and
  the 80 % are H: an easy evening run never drops a night), sleeping at altitude
  (Latshang 2013; inferred from the day's highest point ≥ 1 600 m, H), a time
  zone change (Janse van Rensburg 2021; a step of more than 1 h, so a clock
  change at home is none; the night it shows and the next 2, H), the nights
  D+1 → D+3 after an effort of 6 h or more (« après grosse sortie »: Hynynen
  2010, nightly HR at 130 % after a marathon; D is the day it ended, H), and
  the nights of an alert episode (« FC de nuit haute », H; heart rate alone is
  never a diagnosis: evidence row 3). Santé (v4) reads nothing else: no planned
  race, no check-in (owner, 2026-10-08). The race page keeps its own: J-7 →
  J+7 around its race (H), and the « alcool hier » and « malade » chips
  (Pietilä 2018) a check-in stored before v4 holds.
- COROS nightly HR on a day with a nap stays out of the HR band, the 7-night
  mean and the illness alert until it is shown that COROS leaves the nap out
  of its « Sleep HR » line (docs/sante-v3-data-notes.md).
- Bands (H): ≥ 14 untagged nights in the 60 days before, one band per watch
  (Dial 2025: brands average over different windows); from 7 such nights a
  « provisoire » band (owner decision 2026-10-07), labelled so wherever it is
  read, until 14 — never for the illness alert nor its episodes (specific,
  not sensitive: Quer 2021; a provisional band would make the alert fire on
  noise). HR: median ± 3 bpm;
  HRV: exp(mean ln ± 0.5 SD), SD floored at 0.05 (the SWC convention of
  HRV-guided training); 24-h sleep: median ± 30 min; respiration: median.
- 7-night means need ≥ 3 nights in the last 7 (Plews 2014; Lau 2022).
- Illness alert (H): the HR band of the watch that measured both nights, then
  2 nights in a row each ≥ median + max(2 robust SD, 5 bpm) (Altini & Plews
  2021; Quer 2021: specific, not sensitive). Context-tagged nights (« après
  grosse sortie » included) never fire it; on the race page, neither do the
  nights after its race. Respiration ≥ median + 2/min only backs it up.
"""
import copy
import math
import statistics
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services.health import HRV_METHOD, main_window, nap_windows

BAND_DAYS = 60  # (H)
MIN_BAND_NIGHTS = 14  # (H) a full normal
MIN_PROVISIONAL_NIGHTS = 7  # (H) a « provisoire » normal (owner decision 2026-10-07), until 14
MIN_MEAN_NIGHTS = 3  # of the last 7 (Plews 2014; Lau 2022)
WEEK_DAYS = 7
HR_BAND_BPM = 3  # (H)
HRV_BAND_SD = 0.5  # (H) the SWC convention of HRV-guided training
HRV_SD_FLOOR = 0.05  # (H) ≈ 5 %: a very steady band must not make a 2 % dip a signal
SLEEP_BAND_MIN = 30  # (H)
ALERT_SD, ALERT_MIN_BPM = 2, 5  # (H)
RESP_UP = 2  # breaths/min (H)
SHORT_DAY_MIN = 6 * 60  # ≤ 6 h per 24 h (Craven 2022)
REGULARITY_NIGHTS = 8  # of 28 days, any order (H; Fischer 2021)
RESETTLE_H = 3  # (H) a nap starting this soon after the wake: « rendormi »
LATE_NAP_H = 7  # nap ending < 7 h before onset (Mograss 2022)
LONG_SESSION_MIN = 90  # Myllymäki 2012
LATE_SESSION_GAP = timedelta(hours=2)  # (H) a vigorous session ending this soon before sleep onset (Stutz 2019: ≤ 1 h)
VIGOROUS_HRR = 0.8  # (H) average HR ≥ 80 % of the heart-rate reserve: vigorous (Myllymäki 2012); easy is ≈ 60–70 %
VIGOROUS_MIN = 20  # (H) or this many minutes above it in its laps, else its km splits (an interval session)
ALTITUDE_M = 1600  # (H) Latshang 2013 studied 1 630–2 590 m
TZ_CHANGE_MIN = 60  # (H) a step of MORE than this: a 1-h clock change (DST) at home is no time zone change
TZ_NIGHTS = 3  # the night the change shows and the next 2 (H)
TZ_LOOKBACK = 10  # days (H)
RACE_WINDOW = 7  # J-7 → J+7 (H)
ILL_TAIL = 2  # days after the last « malade » (H)
ILL_MERGE = 3  # « malade » days this close make one episode (H)
ILL_MAX = 14  # days an alert episode can run (H)
AXIS = (time(20, 0), time(12, 0))  # the timing chart's local axis
# whether COROS's « Sleep HR » line leaves a nap out: not shown (no HR curve), see the data notes
COROS_SLEEP_HR_NAP_VERIFIED = False

# context words, as the readouts print them (glyph ◇)
# « ill » is the athlete's own « malade » chip; « alert » an alert episode, never worded as a diagnosis (row 3)
TAG_WORDS = {"long": "après une sortie longue", "late": "sortie intense le soir", "altitude": "en altitude",
             "tz": "fuseau changé", "big": "après grosse sortie", "alcohol": "alcool", "race": "autour de la course",
             "ill": "malade", "alert": "FC de nuit haute", "late_nap": "après sieste tardive"}
EXCLUDING = ("long", "late", "altitude", "tz", "big", "alcohol", "race", "ill", "alert")
EPISODE = ("ill", "alert")  # the nights of an illness episode
_EXCLUDING = frozenset(EXCLUDING)
_VALUE = {"hr": "hr", "hrv": "hrv", "resp": "resp"}
_SOURCE = {"hr": "hr_source", "hrv": "hrv_source", "tst24": "source", "resp": "resp_source"}
CONTEXT = ("long", "late", "altitude", "tz", "big", "alcohol")  # never fire the alert


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
    tags: set = field(default_factory=set)
    resettled: bool = False  # « rendormi »

    @property
    def nap_min(self) -> int:
        return sum(m for _, _, m in self.naps)

    @property
    def tst24(self) -> int | None:
        """Minutes asleep over the day: the main night + the day's naps; unknown without a main episode."""
        return None if self.asleep is None else self.asleep + self.nap_min

    @property
    def bed5(self) -> datetime | None:
        return round5(self.start) if self.start else None

    @property
    def wake5(self) -> datetime | None:
        return round5(self.end) if self.end else None

    @property
    def excluded(self) -> bool:
        """Out of the band and of the 7-night means."""
        return bool(self.tags & set(EXCLUDING))

    def value(self, metric: str):
        """hr, hrv, tst24 or resp (read on every night of every band: no dict built per call)."""
        return self.tst24 if metric == "tst24" else getattr(self, _VALUE[metric])

    def source_of(self, metric: str) -> str | None:
        return getattr(self, _SOURCE[metric])

    def usable(self, metric: str, ignore=()) -> bool:
        """Can enter the band and the 7-night means for `metric` (`ignore`: tags
        that do not exclude here, e.g. EPISODE for the tile that shows an
        illness episode while R2/R3 is the rung, never for what decides)."""
        out = _EXCLUDING - set(ignore) if ignore else _EXCLUDING
        if self.value(metric) is None or not self.tags.isdisjoint(out):
            return False
        if metric == "hr" and self.hr_nap_day and self.hr_method == "coros_sleep_summary":
            return COROS_SLEEP_HR_NAP_VERIFIED
        return True

    def in_axis(self, a: datetime, b: datetime) -> bool:
        """A segment that fits the timing chart's 20:00 → 12:00 axis of this night."""
        lo = datetime.combine(self.day - timedelta(days=1), AXIS[0])
        return lo <= a and b <= datetime.combine(self.day, AXIS[1])


# ── building the nights ─────────────────────────────────────────────────────

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
            n.resp, n.resp_source = v, src
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


def tag_nights(nights: dict[date, Night], sessions=(), races=(), feel: dict[date, dict] | None = None,
               rest: float = 50.0, peak: float = 190.0) -> None:
    """Context tags on each night (see the module docstring), in place.
    `sessions`: sante_training.Session; `races`: [(race day, name)];
    `feel`: {day: feel_of(...)}; `rest`/`peak`: the athlete's HR bounds."""
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
        if any((s.elev_high or 0) >= ALTITUDE_M for s in before + by_day.get(d, [])):
            n.tags.add("altitude")
        if (feel.get(d) or {}).get("alcohol"):
            n.tags.add("alcohol")
        for rd, _ in races:
            if abs((d - rd).days) <= RACE_WINDOW:
                n.tags.add("race")
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


def hr_segments(laps, splits) -> tuple:
    """((minutes, average HR), …) of an activity's laps, else of its km splits
    (Strava's shapes): what « ≥ 20 min above » reads; () without them."""
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


async def load_segments(db: AsyncSession, nights: dict[date, Night], sessions) -> None:
    """Each session that could tag a night « late » (late_candidates) gets its
    laps' (else its splits') HR segments as `segs`, in one read; the others
    keep None (their average alone is read)."""
    from app.models.activity import Activity

    by_day = defaultdict(list)
    for s in sessions:
        by_day[s.day].append(s)
    want = {}
    for d, n in nights.items():
        for s in late_candidates(n, by_day.get(d - timedelta(days=1), []) + by_day.get(d, [])):
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


def _tag_timezones(nights: dict[date, Night], sessions) -> None:
    """« fuseau changé » on the night a new UTC offset shows (the night's own,
    else that of the sessions of the day before or that day) and the next 2."""
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
        if prev is not None and abs(track[d] - track[prev]) > TZ_CHANGE_MIN:
            for k in range(TZ_NIGHTS):
                if d + timedelta(days=k) in nights:
                    nights[d + timedelta(days=k)].tags.add("tz")


def _tag_late_naps(nights: dict[date, Night]) -> None:
    """« après sieste tardive » on the night after a nap that ended < 7 h before its onset."""
    for d, n in nights.items():
        if not n.start:
            continue
        cands = [nights.get(d - timedelta(days=1)), n]
        if any(b and b <= n.start and n.start - b < timedelta(hours=LATE_NAP_H)
               for m in cands if m for _, b, _ in m.naps):
            n.tags.add("late_nap")


def tag_efforts(nights: dict[date, Night], efforts=()) -> None:
    """« après grosse sortie » on the nights after an effort of 6 h or more
    (sante_training.Effort with `big`), in place: D+1 → D+3 (D: the day it
    ended), and a sleep that started after its end on D itself (an ultra
    finished at 03:00). Out of the bands and the illness alert (H; Hynynen
    2010); drawn like any night."""
    from app.services.sante_training import BIG_NIGHTS

    for e in efforts:
        if not e.big:
            continue
        for k in range(BIG_NIGHTS + 1):
            n = nights.get(e.day + timedelta(days=k))
            if n is not None and (k > 0 or (n.start is not None and n.start >= e.end)):
                n.tags.add("big")


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
        n = nights.get(until - timedelta(days=k))
        if n is not None and n.value(metric) is not None:
            return n.source_of(metric)
    lo = until - timedelta(days=WEEK_DAYS)
    last = max((d for d, n in nights.items() if d <= lo and n.value(metric) is not None), default=None)
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
    """The athlete's normal for `metric` (hr, hrv, tst24, resp) from the usable
    nights of the 60 days before `until` (excluded), on one watch (the latest
    one's unless `source`): None under 7 nights, `provisional` from 7 to 13
    nights (« provisoire », H); `full`: None under 14 nights (the illness
    alert and its episodes)."""
    source = source or _latest_source(nights, metric, until - timedelta(days=1))
    lo = until - timedelta(days=BAND_DAYS)
    vals = [n.value(metric) for d, n in nights.items()
            if lo <= d < until and n.usable(metric) and n.source_of(metric) == source]
    out = {"metric": metric, "n": len(vals), "source": source, "until": until,
           "provisional": len(vals) < MIN_BAND_NIGHTS}
    if len(vals) < (MIN_BAND_NIGHTS if full else MIN_PROVISIONAL_NIGHTS):
        return None
    if metric == "hrv":
        logs = [math.log(v) for v in vals if v > 0]
        mean, sd = statistics.fmean(logs), max(statistics.stdev(logs), HRV_SD_FLOOR)
        out.update(center=math.exp(mean), lo=math.exp(mean - HRV_BAND_SD * sd), hi=math.exp(mean + HRV_BAND_SD * sd),
                   sd=sd)
        return out
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals)
    out.update(center=med, sd=1.4826 * mad)
    if metric == "hr":
        out.update(lo=med - HR_BAND_BPM, hi=med + HR_BAND_BPM, alert=med + max(ALERT_SD * 1.4826 * mad, ALERT_MIN_BPM))
    elif metric == "tst24":
        out.update(lo=med - SLEEP_BAND_MIN, hi=med + SLEEP_BAND_MIN)
    else:  # resp: no band drawn, only « + 2/min » backs the alert
        out.update(lo=None, hi=None, up=med + RESP_UP)
    return out


def mean_source(nights: dict[date, Night], metric: str, today: date) -> str | None:
    """The watch the 7-night mean of `today` reads (the latest one that measured
    `metric`): its value is judged against that watch's band only (Dial 2025)."""
    return _latest_source(nights, metric, today)


def mean7(nights: dict[date, Night], metric: str, today: date, untagged: bool = True, ignore=()) -> dict | None:
    kept = _kept(nights)
    if kept is None:
        return _mean7(nights, metric, today, untagged, ignore)
    key = ("mean7", metric, today, untagged, tuple(ignore))
    if key not in kept:
        kept[key] = _mean7(nights, metric, today, untagged, ignore)
    return kept[key]


def _mean7(nights: dict[date, Night], metric: str, today: date, untagged: bool = True, ignore=()) -> dict | None:
    """The mean of the last 7 days (HRV: exp of the mean ln), {value, n}; None
    under 3 nights. `untagged=False` keeps excluded nights in (the 24-h sleep
    tile shows its value in race week; it is just never judged); `ignore`:
    tags that do not exclude a night here (Night.usable)."""
    source = _latest_source(nights, metric, today)
    lo = today - timedelta(days=6)
    vals = [n.value(metric) for d, n in nights.items()
            if lo <= d <= today and n.value(metric) is not None
            and n.source_of(metric) == source and (n.usable(metric, ignore) if untagged else True)]
    if len(vals) < MIN_MEAN_NIGHTS:
        return None
    v = math.exp(statistics.fmean(math.log(x) for x in vals)) if metric == "hrv" else statistics.fmean(vals)
    return {"value": v, "n": len(vals)}


def status(value: float | None, b: dict | None) -> str | None:
    """« above » / « below » / « in » against the band; None without both."""
    if value is None or not b or b.get("lo") is None:
        return None
    return "above" if value > b["hi"] else "below" if value < b["lo"] else "in"


# ── timing ──────────────────────────────────────────────────────────────────

def timing(nights: dict[date, Night], today: date, days: int = 28) -> dict:
    """Median onset and wake (minutes after 18:00) over the main nights of the
    last `days` days, and their spread (SD) once 8 nights are there (H). Time
    zone nights, the nights after a big effort and those around a race (the
    race page) are left out; a « rendormi » wake counts in neither the wake
    median nor its spread (H)."""
    ns = [n for d, n in nights.items() if today - timedelta(days=days - 1) <= d <= today and n.start
          and not n.tags & {"tz", "big", "race"}]
    beds = [clock_min(n.start) for n in ns]
    wakes = [clock_min(n.end) for n in ns if not n.resettled]
    out = {"n": len(ns), "bed": statistics.median(beds) if beds else None,
           "wake": statistics.median(wakes) if wakes else None, "bed_sd": None, "wake_sd": None,
           "regular_ok": len(ns) >= REGULARITY_NIGHTS}
    if out["regular_ok"]:
        out["bed_sd"] = statistics.stdev(beds)
        out["wake_sd"] = statistics.stdev(wakes) if len(wakes) >= REGULARITY_NIGHTS else None
    return out


# ── illness alert ───────────────────────────────────────────────────────────

def alert_night(nights: dict[date, Night], races, d: date) -> bool:
    """A night that may fire the alert: measured HR, no context tag, not after a
    race (`races`: [(day, name)], Routes and sessions marked as races)."""
    n = nights.get(d)
    if not n or n.hr is None or n.tags & set(CONTEXT):
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
    in place: out of the band and the means like « malade », never worded as it."""
    for d in alert_episodes(nights, today, races):
        if d in nights:
            nights[d].tags.add("alert")


SANTE_ROWS = ("sleep", "nap", "hrv", "hr_night", "resp_night")  # Santé v4: no check-in


def tag_activities(nights: dict[date, Night], sessions=(), efforts=(), rest: float = 50.0,
                   peak: float = 190.0) -> None:
    """Santé v4's tags, from the activities alone, in place: the context of
    each night (sortie longue la veille, sortie intense le soir, altitude,
    fuseau) and « après grosse sortie » (`efforts`: sante_training.efforts).
    No planned race, no check-in; « FC de nuit haute » comes after (tag_alerts)."""
    tag_nights(nights, sessions, (), {}, rest, peak)
    tag_efforts(nights, efforts)


async def load_nights(db: AsyncSession, user_id: int, today: date, days: int = 400, sessions=(), efforts=(),
                      rest: float | None = None, peak: float = 190.0) -> dict[date, Night]:
    """Santé's nights of the last `days` days, tagged from the activities
    (tag_activities), then « FC de nuit haute » (the alert episodes). `rest`
    None: from the nights (rest_hr)."""
    rows = await read_rows(db, user_id, today - timedelta(days=days), today, SANTE_ROWS)
    nights = build_nights(rows, today)
    if rest is None:
        rest = rest_hr(nights, today)
    await load_segments(db, nights, sessions)
    tag_activities(nights, sessions, efforts, rest, peak)
    tag_alerts(nights, today)
    return nights
