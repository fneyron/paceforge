"""The plan's clock: seconds after the start ↔ the wall clock the athlete reads.

The model's currency is seconds after the start (moving time, stops, cutoff margins, pinned times). What the
plan shows is the wall clock of the race's own zone, carried as ``day × 86400 + seconds of that local day``
(day 0 = the race date): the table, the printed band, the phone and the exports print it as HH:MM with a day
separator. Without a zone (no date, no place: race_match.race_clock) the clock is the start's offset plus the
elapsed seconds, as the plan always read. With one, the start instant is built in UTC (race date, local start
time, ZoneInfo) and the elapsed seconds are added, then read in the zone: a race crossing a daylight-saving
change shows the real wall clock (22:00 + 5 h 39 is 02:39 on the autumn night, not 03:39). A local time read
back into seconds after the start (a cutoff, a passage typed on race day) is its FIRST occurrence (fold 0):
the repeated autumn hour is its summer reading, a time inside the spring gap is read with the offset before
the change. The rows keep their own elapsed seconds (``elapsed_s`` / ``adjusted_elapsed_s``), so nothing
re-derives them from a clock that may have jumped.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.timezones import known_zone


class PlanClock:
    """Elapsed seconds ↔ plan clock for one race start. ``start_offset_s``: seconds after midnight of the race
    day; ``race_date``: YYYY-MM-DD or a date; ``tz``: the zone's IANA name. Without a date or a known zone the
    clock is ``start_offset_s`` + elapsed."""

    __slots__ = ("start_offset_s", "zone", "_day0", "_start_utc")

    def __init__(self, start_offset_s: int, race_date=None, tz: str | None = None):
        self.start_offset_s = int(start_offset_s)
        self.zone = known_zone(tz)
        self._day0 = self._start_utc = None
        if self.zone is not None:
            try:
                d = race_date if isinstance(race_date, date) else date.fromisoformat(str(race_date)[:10])
            except (TypeError, ValueError):
                d = None
            if d is None:
                self.zone = None
            else:
                # wall-clock arithmetic, fold 0: a start inside the spring gap reads with the offset before the change
                local = datetime(d.year, d.month, d.day, tzinfo=self.zone) + timedelta(seconds=self.start_offset_s)
                self._day0, self._start_utc = d, local.astimezone(timezone.utc)

    @property
    def zoned(self) -> bool:
        return self.zone is not None

    def clock_s(self, elapsed_s: float) -> int:
        """The plan clock ``elapsed_s`` seconds after the start."""
        if self.zone is None:
            return int(self.start_offset_s + elapsed_s)
        local = (self._start_utc + timedelta(seconds=elapsed_s)).astimezone(self.zone)
        return (local.date() - self._day0).days * 86400 + local.hour * 3600 + local.minute * 60 + local.second

    def elapsed_s(self, clock_s: float) -> int:
        """Seconds after the start of a plan clock: the first occurrence of an ambiguous local time."""
        if self.zone is None:
            return int(clock_s - self.start_offset_s)
        day, sod = divmod(int(clock_s), 86400)
        d = self._day0 + timedelta(days=day)
        local = datetime(d.year, d.month, d.day, tzinfo=self.zone) + timedelta(seconds=sod)
        return int(round((local.astimezone(timezone.utc) - self._start_utc).total_seconds()))

    def elapsed_of(self, section: dict, plan: bool) -> int | None:
        """Seconds after the start at the end of a passage section, stops included: the plan's (``plan``) or
        the prediction's; from the row, else from its clock (a row built before the elapsed was stored); None
        when the row has no such clock."""
        e = section.get("adjusted_elapsed_s" if plan else "elapsed_s")
        if e is not None:
            return int(e)
        c = section.get("adjusted_clock_time_s" if plan else "clock_time_s")
        return self.elapsed_s(c) if c is not None else None
