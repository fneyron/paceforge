"""Santé v3: PaceForge's own nightly values, read back once per page.

One `Night` per wake-up day, from the rows the watches' syncs write
(health.py docstring): the main window and minutes asleep, the day's naps, the
24-h total (main night + naps), nightly HR, HRV and respiration, and the
context that keeps a night out of the athlete's normal. Pure functions on
those rows; `load_nights` reads them.

Rules, with where they come from (evidence_final.md; (H) = a PaceForge
heuristic, never shown as a finding):
- Naps count in the 24-h total only, never in timing, regularity, nightly
  HR/HRV or the hypnogram (Mollicone 2008; Romyn 2018). A nap belongs to the
  day it ends. A day with naps but no main episode has no 24-h total (« nuit
  incomplète ? »).
- Times are the watch's detection, approximate: printed rounded to 5 min
  (de Zambotti 2024).
- « Rendormi » (H): a nap starting ≤ 3 h after the main wake leaves that wake
  out of the wake median and spread, with no wake-shift word.
- « Après sieste tardive »: a nap ending < 7 h before the next main onset
  annotates that night (Mograss 2022); it does not exclude it.
- Excluded nights (H), out of the band and of the 7-night means: a session
  ≥ 90 min the day before (Myllymäki 2012), a vigorous session ending ≤ 1 h
  before sleep onset (Stutz 2019) or a hard evening session (Myllymäki 2012;
  the 17:00 start that defines « evening » is H), sleeping at altitude
  (Latshang 2013; inferred from the day's highest point ≥ 1 600 m, H), a time
  zone change (Janse van Rensburg 2021; the night it shows and the next 2, H),
  the « alcool hier » chip (Pietilä 2018), J-7 → J+7 around a race (H), and
  the nights of an illness episode (H).
- COROS nightly HR on a day with a nap stays out of the HR band, the 7-night
  mean and the illness alert until it is shown that COROS leaves the nap out
  of its « Sleep HR » line (docs/sante-v3-data-notes.md).
- Bands (H): ≥ 14 untagged nights in the 60 days before, one band per watch
  (Dial 2025: brands average over different windows). HR: median ± 3 bpm;
  HRV: exp(mean ln ± 0.5 SD), SD floored at 0.05 (the SWC convention of
  HRV-guided training); 24-h sleep: median ± 30 min; respiration: median.
- 7-night means need ≥ 3 nights in the last 7 (Plews 2014; Lau 2022).
- Illness alert (H): the HR band, then 2 nights in a row each ≥ median +
  max(2 robust SD, 5 bpm) (Altini & Plews 2021; Quer 2021: specific, not
  sensitive). Context-tagged nights and the nights after a race never fire it;
  race week does (J-7 → J-1). Respiration ≥ median + 2/min only backs it up.
- « Reprise » (H, after Schwellnus 2022; Snyders 2022; Radin 2021): opened by
  the « malade » chip or the alert, closed when there is no « malade » on the
  last 2 days, the first easy run since is within 3 bpm of the athlete's usual
  (when measurable) and nightly HR is back in the band or falling; still open
  after 14 days → « vois un médecin ».
"""
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health import HealthMetric
from app.services.health import HRV_METHOD, feel_of, main_window, nap_windows

BAND_DAYS = 60  # (H)
MIN_BAND_NIGHTS = 14  # (H)
MIN_MEAN_NIGHTS = 3  # of the last 7 (Plews 2014; Lau 2022)
HR_BAND_BPM = 3  # (H)
HRV_BAND_SD = 0.5  # (H) the SWC convention of HRV-guided training
HRV_SD_FLOOR = 0.05  # (H) ≈ 5 %: a very steady band must not make a 2 % dip a signal
SLEEP_BAND_MIN = 30  # (H)
ALERT_SD, ALERT_MIN_BPM = 2, 5  # (H)
RESP_UP = 2  # breaths/min (H)
SHORT_DAY_MIN = 6 * 60  # ≤ 6 h per 24 h (Craven 2022)
QUIET_LINE_MIN = 7 * 60  # 14-night mean under ≈ 7 h (Johnston 2020)
EARLY_WAKE_MIN = 60  # (H) before the median wake
REGULARITY_NIGHTS = 8  # of 28 days, any order (H; Fischer 2021)
RESETTLE_H = 3  # (H) a nap starting this soon after the wake: « rendormi »
LATE_NAP_H = 7  # nap ending < 7 h before onset (Mograss 2022)
LONG_SESSION_MIN = 90  # Myllymäki 2012
LATE_SESSION_GAP = timedelta(hours=1)  # Stutz 2019
EVENING = time(17, 0)  # (H) a hard session starting after 17:00 is an « evening » one
VIGOROUS_HRR = 0.6  # (H) average HR ≥ 60 % of the heart-rate reserve
ALTITUDE_M = 1600  # (H) Latshang 2013 studied 1 630–2 590 m
TZ_CHANGE_MIN = 60  # (H)
TZ_NIGHTS = 3  # the night the change shows and the next 2 (H)
TZ_LOOKBACK = 10  # days (H)
RACE_WINDOW = 7  # J-7 → J+7 (H)
ILL_TAIL = 2  # days after the last « malade » (H)
ILL_MERGE = 3  # « malade » days this close make one episode (H)
ILL_MAX = 14  # days an alert episode can run (H)
REPRISE_LOOKBACK = 28  # days (H)
REPRISE_EASY_BPM = 3  # (H)
REPRISE_DOCTOR = 14  # days (H)
AXIS = (time(20, 0), time(12, 0))  # the timing chart's local axis
# whether COROS's « Sleep HR » line leaves a nap out: not shown (no HR curve), see the data notes
COROS_SLEEP_HR_NAP_VERIFIED = False

# context words, as the readouts print them (glyph ◇)
TAG_WORDS = {"long": "après une longue séance", "late": "séance intense le soir", "altitude": "en altitude",
             "tz": "fuseau changé", "alcohol": "alcool", "race": "autour de la course", "ill": "malade",
             "late_nap": "après sieste tardive"}
EXCLUDING = ("long", "late", "altitude", "tz", "alcohol", "race", "ill")
CONTEXT = ("long", "late", "altitude", "tz", "alcohol")  # never fire the alert


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
        return {"hr": self.hr, "hrv": self.hrv, "tst24": self.tst24, "resp": self.resp}[metric]

    def source_of(self, metric: str) -> str | None:
        return {"hr": self.hr_source, "hrv": self.hrv_source, "tst24": self.source, "resp": self.resp_source}[metric]

    def usable(self, metric: str, ignore=()) -> bool:
        """Can enter the band and the 7-night means for `metric` (`ignore`: tags
        that do not exclude here, e.g. « ill » for the tile shown during an
        illness episode)."""
        if self.value(metric) is None or (self.tags - set(ignore)) & set(EXCLUDING):
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


def day_tst24(nights: dict[date, Night], d: date) -> int | None:
    """The morning decision's 24-h total (H): the main night of `d`, the naps
    of `d` synced so far, and those of the day before that ended within the
    24 h before the main wake (a pre-race afternoon nap counts). None without a
    main episode. The charts keep a nap on the day it ends (Night.tst24)."""
    n = nights.get(d)
    if not n or n.asleep is None:
        return None
    prev = nights.get(d - timedelta(days=1))
    extra = sum(m for a, b, m in (prev.naps if prev else []) if b and b >= n.end - timedelta(hours=24))
    return n.tst24 + extra


# ── context tags ────────────────────────────────────────────────────────────

def _local(s, t: datetime) -> datetime:
    return (t + timedelta(seconds=s.offset or 0)).replace(tzinfo=None)


def _vigorous(s, rest: float, peak: float) -> bool:
    return bool(s.hr) and s.hr >= rest + VIGOROUS_HRR * (peak - rest)


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
        for s in before + by_day.get(d, []):
            end = _local(s, s.start) + timedelta(minutes=s.elapsed or s.minutes)
            start = _local(s, s.start)
            if not _vigorous(s, rest, peak):
                continue
            if n.start and timedelta(0) <= n.start - end <= LATE_SESSION_GAP:
                n.tags.add("late")  # ending ≤ 1 h before sleep onset (Stutz 2019)
            elif s.day == d - timedelta(days=1) and start.time() >= EVENING and (not n.start or end <= n.start):
                n.tags.add("late")  # a hard evening session (Myllymäki 2012; 17:00 is H)
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
        if prev is not None and abs(track[d] - track[prev]) >= TZ_CHANGE_MIN:
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


def illness_days(feel: dict[date, dict], alert_days=()) -> set[date]:
    """The days of an illness episode (H): « malade » days within 3 days of each
    other make one episode, which runs 2 days after the last; plus the days an
    alert episode ran (from alert_episodes)."""
    sick = sorted(d for d, f in (feel or {}).items() if f and "sick" in (f.get("why") or []))
    out = set(alert_days)
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
    for d in sorted(nights, reverse=True):
        if d <= until and nights[d].value(metric) is not None:
            return nights[d].source_of(metric)
    return None


def band(nights: dict[date, Night], metric: str, until: date, source: str | None = None) -> dict | None:
    """The athlete's normal for `metric` (hr, hrv, tst24, resp) from the usable
    nights of the 60 days before `until` (excluded), on one watch (the latest
    one's unless `source`): None under 14 nights (« ta normale se construit »)."""
    source = source or _latest_source(nights, metric, until - timedelta(days=1))
    vals = [n.value(metric) for d, n in nights.items()
            if until - timedelta(days=BAND_DAYS) <= d < until and n.usable(metric) and n.source_of(metric) == source]
    out = {"metric": metric, "n": len(vals), "source": source, "until": until}
    if len(vals) < MIN_BAND_NIGHTS:
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


def band_count(nights: dict[date, Night], metric: str, until: date) -> int:
    """How many usable nights the band would rest on (« ta normale se construit (n/14 nuits) »)."""
    source = _latest_source(nights, metric, until - timedelta(days=1))
    return sum(1 for d, n in nights.items() if until - timedelta(days=BAND_DAYS) <= d < until
               and n.usable(metric) and n.source_of(metric) == source)


def mean7(nights: dict[date, Night], metric: str, today: date, untagged: bool = True, ignore=()) -> dict | None:
    """The mean of the last 7 days (HRV: exp of the mean ln), {value, n}; None
    under 3 nights. `untagged=False` keeps excluded nights in (the 24-h sleep
    tile shows its value in race week; it is just never judged); `ignore`:
    tags that do not exclude a night here (Night.usable)."""
    source = _latest_source(nights, metric, today)
    vals = [n.value(metric) for d, n in nights.items()
            if today - timedelta(days=6) <= d <= today and n.value(metric) is not None
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


def quiet_short_sleep(nights: dict[date, Night], today: date) -> bool:
    """« Moins de 7 h en moyenne sur tes nuits mesurées des 14 derniers jours. »
    (Johnston 2020): the mean 24-h total of the measured days, 3 at least."""
    vals = [n.tst24 for d, n in nights.items() if today - timedelta(days=13) <= d <= today and n.tst24 is not None]
    return len(vals) >= MIN_MEAN_NIGHTS and statistics.fmean(vals) < QUIET_LINE_MIN


# ── timing ──────────────────────────────────────────────────────────────────

def timing(nights: dict[date, Night], today: date, days: int = 28) -> dict:
    """Median onset and wake (minutes after 18:00) over the main nights of the
    last `days` days, and their spread (SD) once 8 nights are there (H). Time
    zone and race nights are left out; a « rendormi » wake counts in neither
    the wake median nor its spread (H)."""
    ns = [n for d, n in nights.items() if today - timedelta(days=days - 1) <= d <= today and n.start
          and not n.tags & {"tz", "race"}]
    beds = [clock_min(n.start) for n in ns]
    wakes = [clock_min(n.end) for n in ns if not n.resettled]
    out = {"n": len(ns), "bed": statistics.median(beds) if beds else None,
           "wake": statistics.median(wakes) if wakes else None, "bed_sd": None, "wake_sd": None,
           "regular_ok": len(ns) >= REGULARITY_NIGHTS}
    if out["regular_ok"]:
        out["bed_sd"] = statistics.stdev(beds)
        out["wake_sd"] = statistics.stdev(wakes) if len(wakes) >= REGULARITY_NIGHTS else None
    return out


def early_wake(night: Night, usual: dict) -> bool:
    """A wake ≥ 60 min before the median wake (H), or no median to compare with."""
    if night.end is None:
        return False
    if usual.get("wake") is None:
        return True
    return clock_min(night.end) <= usual["wake"] - EARLY_WAKE_MIN


def nap_tip_hour(usual: dict) -> str:
    """{h} of « Une sieste de 20 à 90 min avant {h} aide » : median bedtime − 7 h,
    capped at 16:00 (Mograss 2022; Lastella 2021), rounded to 5 min."""
    if usual.get("bed") is None:
        return "16:00"
    m = min(11 * 60 + usual["bed"], 16 * 60)  # 18:00 + bed − 7 h, as minutes after midnight
    m = int(round(m / 5)) * 5
    return f"{m // 60:02d}:{m % 60:02d}"


# ── illness alert and « Reprise » ───────────────────────────────────────────

def alert_night(nights: dict[date, Night], races, d: date) -> bool:
    """A night that may fire the alert: measured HR, no context tag, not after a race."""
    n = nights.get(d)
    if not n or n.hr is None or n.tags & set(CONTEXT):
        return False
    if n.hr_nap_day and n.hr_method == "coros_sleep_summary" and not COROS_SLEEP_HR_NAP_VERIFIED:
        return False
    return not any(0 <= (d - rd).days <= RACE_WINDOW for rd, _ in races)  # race week may fire it, not after


def illness_alert(nights: dict[date, Night], today: date, races=()) -> dict | None:
    """The two nights in a row (yesterday's and today's) each ≥ the HR band's
    alert line, against the band of the 60 days before them: {days, values,
    threshold, resp_up}; None otherwise (no band, a night missing or tagged)."""
    d2, d1 = today, today - timedelta(days=1)
    b = band(nights, "hr", d1)
    if not b or not all(alert_night(nights, races, d) for d in (d1, d2)):
        return None
    vals = [nights[d1].hr, nights[d2].hr]
    if not all(v >= b["alert"] for v in vals):
        return None
    rb = band(nights, "resp", d1)
    resp = [nights[d].resp for d in (d1, d2)]
    resp_up = (all(r is not None and r >= rb["up"] for r in resp)) if rb else None
    return {"days": [d1, d2], "values": vals, "threshold": b["alert"], "resp_up": resp_up}


def alert_episodes(nights: dict[date, Night], today: date, races=(), days: int = BAND_DAYS + 7) -> set[date]:
    """The nights of the alert episodes of the last `days` days (H): from the
    first of the 2 nights, while the measured nights stay above the band (14
    days at most)."""
    out: set[date] = set()
    d = today - timedelta(days=days)
    while d <= today:
        a = illness_alert(nights, d, races)
        if a and a["days"][0] not in out:
            b = band(nights, "hr", a["days"][0])
            k = a["days"][0]
            while k <= min(today, a["days"][0] + timedelta(days=ILL_MAX)):
                n = nights.get(k)
                if n and n.hr is not None and n.hr <= b["hi"] and k > a["days"][1]:
                    break
                out.add(k)
                k += timedelta(days=1)
        d += timedelta(days=1)
    return out


def reprise(nights: dict[date, Night], feel: dict[date, dict], today: date, races=(),
            easy: list[tuple[date, float]] = ()) -> dict | None:
    """The « Reprise » state (see the module docstring): {since, days, cause,
    gates: {no_sick, easy_hr, night_hr}, see_doctor}; None when no episode
    opened in the last 28 days or it has closed. `easy`: [(day, bpm against the
    athlete's usual at that pace)] of the flat easy runs (sante_training)."""
    lo = today - timedelta(days=REPRISE_LOOKBACK)
    sick = {d for d, f in (feel or {}).items() if lo <= d <= today and f and "sick" in (f.get("why") or [])}
    alerts = {d for d in (today - timedelta(days=k) for k in range(REPRISE_LOOKBACK + 1))
              if illness_alert(nights, d, races)}
    opens = sorted(sick | alerts)
    if not opens:
        return None
    start = opens[-1]
    for d in reversed(opens[:-1]):  # the episode's first day
        if (start - d).days <= ILL_MERGE:
            start = d
    cause = "malade" if start in sick else "alert"
    no_sick = not ({today, today - timedelta(days=1)} & sick) and today not in alerts
    before = [v for d, v in easy if start - timedelta(days=60) <= d < start]
    after = sorted((d, v) for d, v in easy if d > start)
    easy_ok = True if not before else bool(after) and abs(after[0][1]) <= REPRISE_EASY_BPM
    b = band(nights, "hr", start)
    since = [(d, n.hr) for d, n in sorted(nights.items()) if d > start and n.hr is not None]
    if not since:
        night_ok = True  # not measured: never held for not wearing the watch
    else:
        last = since[-1][1]
        night_ok = (b is not None and last <= b["hi"]) or (len(since) >= 2 and last < max(v for _, v in since))
    gates = {"no_sick": no_sick, "easy_hr": easy_ok, "night_hr": night_ok}
    if all(gates.values()):
        return None
    days = (today - start).days
    return {"since": start, "days": days, "cause": cause, "gates": gates, "see_doctor": days >= REPRISE_DOCTOR}


# ── reading ─────────────────────────────────────────────────────────────────

NIGHT_ROWS = ("sleep", "nap", "hrv", "hr_night", "resp_night", "feel")


async def read_rows(db: AsyncSession, user_id: int, lo: date, hi: date) -> dict[str, dict[date, tuple]]:
    """{metric: {day: (value, details, source)}} of the nightly rows and the check-ins."""
    rows = await db.execute(
        select(HealthMetric.metric, HealthMetric.date, HealthMetric.value, HealthMetric.details,
               HealthMetric.source)
        .where(HealthMetric.user_id == user_id, HealthMetric.metric.in_(NIGHT_ROWS),
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


async def load_nights(db: AsyncSession, user_id: int, today: date, days: int = 400, sessions=(),
                      races=(), rest: float | None = None,
                      peak: float = 190.0) -> tuple[dict[date, Night], dict[date, dict]]:
    """(nights, check-ins) of the last `days` days, tagged: context, race
    window, illness (« malade » chip, then the alert episodes found with the
    band built without them). `rest` None: from the nights (rest_hr)."""
    rows = await read_rows(db, user_id, today - timedelta(days=days), today)
    feel = {d: feel_of(v, det) for d, (v, det, _) in rows.get("feel", {}).items()}
    nights = build_nights(rows, today)
    if rest is None:
        rest = rest_hr(nights, today)
    tag_nights(nights, sessions, races, feel, rest, peak)
    for d in alert_episodes(nights, today, races):
        if d in nights:
            nights[d].tags.add("ill")
    return nights, feel
