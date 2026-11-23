from datetime import date, datetime
from typing import List
from zoneinfo import ZoneInfo

from icalendar import Calendar

from config import TIMEZONE
from fetch import get
from models import Event, Venue

_TZ = ZoneInfo(TIMEZONE)


def _split(value):
    """Return (YYYY-MM-DD, HH:MM or None) in Pacific time."""
    if value is None:
        return None, None
    dt = value.dt
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            dt = dt.astimezone(_TZ)
        return dt.date().isoformat(), dt.strftime("%H:%M")
    if isinstance(dt, date):
        return dt.isoformat(), None
    return None, None


def events_from_ics(ics_url: str, venue: Venue) -> List[Event]:
    cal = Calendar.from_ical(get(ics_url).content)
    events = []
    for comp in cal.walk("VEVENT"):
        start_date, start_time = _split(comp.get("dtstart"))
        end_date, end_time = _split(comp.get("dtend"))
        events.append(
            Event(
                title=str(comp.get("summary", "")).strip(),
                venue=venue.name,
                event_type="other",
                start_date=start_date,
                end_date=end_date,
                start_time=start_time,
                end_time=end_time,
                description=str(comp.get("description", "")).strip()[:500],
                url=str(comp.get("url", "")) or venue.page_urls[0],
                source="ics",
            )
        )
    return events
