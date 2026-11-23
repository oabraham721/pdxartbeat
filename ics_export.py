"""Write a subscribable iCalendar feed (events.ics) from cleaned events."""
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from icalendar import Calendar
from icalendar import Event as ICalEvent

from config import TIMEZONE
from models import Event

_TZ = ZoneInfo(TIMEZONE)
DEFAULT_DURATION = timedelta(hours=2)


def _dt(day: str, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.fromisoformat(day).replace(hour=h, minute=m, tzinfo=_TZ)


def _ical_event(e: Event, uid: str, summary: str, day: str, timed: bool) -> ICalEvent:
    ev = ICalEvent()
    ev.add("uid", f"{uid}@pdxartbeat")
    ev.add("summary", summary)
    ev.add("dtstamp", datetime.now(_TZ))
    if timed and e.start_time:
        start = _dt(day, e.start_time)
        end = _dt(day, e.end_time) if e.end_time and e.end_time > e.start_time else start + DEFAULT_DURATION
        ev.add("dtstart", start)
        ev.add("dtend", end)
    else:
        d = date.fromisoformat(day)
        ev.add("dtstart", d)
        ev.add("dtend", d + timedelta(days=1))
    ev.add("location", f"{e.venue}, {e.address}, Portland, OR" if e.address else f"{e.venue}, Portland, OR")
    desc = " ".join(p for p in [e.description, ", ".join(e.artists), e.price_note] if p)
    if desc:
        ev.add("description", desc)
    if e.url:
        ev.add("url", e.url)
    return ev


def _components(e: Event, today: str):
    """Calendar entries for one event: one-off events as themselves; exhibitions as a "Last day" (or "Opens") reminder."""
    if e.event_type == "exhibition":
        # A month-long all-day block is calendar clutter; remind people of the last day instead.
        if e.end_date:
            return [_ical_event(e, f"{e.id}-last", f"Last day: {e.title} ({e.venue})", e.end_date, timed=False)]
        if e.start_date and e.start_date > today:
            return [_ical_event(e, f"{e.id}-opens", f"Opens: {e.title} ({e.venue})", e.start_date, timed=False)]
        return []
    if e.start_date:
        return [_ical_event(e, e.id, f"{e.title} @ {e.venue}", e.start_date, timed=True)]
    return []


def _calendar(name: str) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//PDX Artbeat//Portland art events//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", TIMEZONE)
    return cal


def _today() -> str:
    return datetime.now(_TZ).date().isoformat()


def write_ics(events: Iterable[Event], path: Path) -> int:
    cal = _calendar("PDX Artbeat")
    today = _today()
    count = 0
    for e in events:
        for comp in _components(e, today):
            cal.add_component(comp)
            count += 1
    cal.add_missing_timezones()
    path.write_bytes(cal.to_ical())
    return count


def write_event_ics(e: Event, path: Path) -> int:
    """Single-event .ics for the site's "Apple / Outlook" button. Returns 1 if written, 0 if the event has no calendar entry."""
    comps = _components(e, _today())
    if not comps:
        return 0
    cal = _calendar(e.title)
    for comp in comps:
        cal.add_component(comp)
    cal.add_missing_timezones()
    path.write_bytes(cal.to_ical())
    return 1
