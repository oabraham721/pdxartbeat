import csv
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from config import ACTIVE_STATUSES, VENUES_CSV


@dataclass
class Venue:
    name: str
    type: str
    neighborhood: str
    address: str
    website: str
    events_url: str
    feed: str
    status: str
    notes: str

    @property
    def page_urls(self) -> List[str]:
        """events_url may list several pages separated by ' | '."""
        urls = [u.strip() for u in (self.events_url or "").split("|") if u.strip()]
        return urls or ([self.website] if self.website else [])


@dataclass
class Event:
    title: str
    venue: str
    event_type: str
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None
    start_time: Optional[str] = None  # HH:MM, 24h, Pacific
    end_time: Optional[str] = None
    artists: List[str] = field(default_factory=list)
    description: str = ""
    url: str = ""
    image_url: str = ""
    neighborhood: str = ""
    address: str = ""
    price: str = "unknown"  # see config.PRICE_TYPES
    price_note: str = ""
    source: str = ""  # "ics" or "ai"
    id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def event_id(venue: str, title: str, event_type: str, start_date: Optional[str]) -> str:
    """Stable id so the same event keeps its id across runs (used for URLs and the archive)."""
    norm = re.sub(r"\W+", "", title.lower())
    return hashlib.sha1(f"{venue}|{norm}|{event_type}|{start_date or ''}".encode()).hexdigest()[:12]


def load_venues(include_inactive: bool = False) -> List[Venue]:
    with open(VENUES_CSV, newline="") as f:
        venues = [Venue(**row) for row in csv.DictReader(f)]
    if include_inactive:
        return venues
    return [v for v in venues if v.status in ACTIVE_STATUSES]
