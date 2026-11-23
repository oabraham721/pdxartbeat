import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

VENUES_CSV = BASE_DIR / "venues.csv"
EVENTS_JSON = BASE_DIR / "events.json"
ARCHIVE_JSON = BASE_DIR / "archive.json"
EVENTS_ICS = BASE_DIR / "events.ics"
VENUE_DETAILS_JSON = BASE_DIR / "venue_details.json"

TIMEZONE = "America/Los_Angeles"
ACTIVE_STATUSES = {"active", "relocating", "occasional"}

EVENT_TYPES = [
    "exhibition",
    "opening_reception",
    "closing_reception",
    "artist_talk",
    "workshop",
    "performance",
    "screening",
    "art_walk",
    "fair",
    "other",
]

PRICE_TYPES = ["free", "suggested_donation", "ticketed", "unknown"]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
MAX_PAGE_CHARS = 20000
