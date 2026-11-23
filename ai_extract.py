import json
import logging
from typing import List

from openai import OpenAI

from config import EVENT_TYPES, OPENAI_API_KEY, OPENAI_MODEL, PRICE_TYPES
from models import Event, Venue

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set (add it to art_atttendor/.env)")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


SYSTEM_PROMPT = f"""You extract art events from the text of a Portland, Oregon gallery or museum web page.

Return JSON: {{"events": [ ... ]}}. Each event object has:
- "title": exhibition or event title (if untitled, use the artist names). Receptions, talks and art-walk nights must say what they are for, e.g. "Opening Reception: Teagan White — Wallowing" or "First Thursday: Quiet Migrations" — never a bare "Opening Reception" or "First Thursday".
- "event_type": one of {EVENT_TYPES}
  - "exhibition" = a show running over a date range
  - "opening_reception" / "closing_reception" = a reception for a show (emit these as SEPARATE events from the exhibition itself)
  - "art_walk" = First Thursday / Last Thursday style events
- "start_date", "end_date": "YYYY-MM-DD" or null. Infer the year from context; if missing, assume the nearest upcoming year relative to today.
- "start_time", "end_time": 24h "HH:MM" or null. Use the event's own stated time only. Never use the gallery's regular opening hours as an event time (e.g. "Gallery open Tue–Sat 11–5" is NOT a First Thursday time); use null if the event time isn't given.
- "artists": list of artist names (may be empty)
- "description": one sentence, max 30 words, or ""
- "url": the most specific [link: ...] URL for this event, or ""
- "image_url": the most relevant [image: ...] URL showing the artwork or event, or "". Never use logos, icons, banners, or payment badges.
- "price": one of {PRICE_TYPES}
  - "free" = explicitly free, or a gallery exhibition/opening with no admission mentioned (commercial galleries are free to enter)
  - "suggested_donation" = pay-what-you-can or suggested donation
  - "ticketed" = requires paid admission or a ticket (including museum admission)
  - "unknown" = cannot tell
- "price_note": short cost detail if stated (e.g. "$25 general admission", "Free with RSVP"), else ""

Rules:
- Only include events that are current or upcoming relative to today (end date, or start date if no end date, is today or later). Skip past and archived shows, including pages or sections headed with a past month (e.g. "Current exhibition, February 2026" when today is later).
- An exhibition listed under a "Current" / "Now on view" heading with no dates may be included with null dates.
- Never create events from generic recurring statements (e.g. "receptions are held the last Thursday of every month") unless a specific date is given.
- Skip permanent collections, generic hours, merchandise and museum-store sales/promotions, newsletters, and calls for artists.
- The text may contain several pages, each starting with "=== PAGE: <url> ===". If the same event appears on several pages, return it once with the most complete details.
- Do not invent dates or details. If the page only gives an end date (e.g. "on view through Dec 12"), set start_date to null; never use today's date as a placeholder. If the page has no qualifying events, return {{"events": []}}.
"""


def extract_events(page_text: str, page_url: str, venue: Venue, today: str) -> List[Event]:
    user_msg = (
        f"Today is {today}.\n"
        f"Venue: {venue.name} ({venue.address or 'Portland, OR'})\n"
        f"Page URL: {page_url}\n\n"
        f"PAGE TEXT:\n{page_text}"
    )
    resp = _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")

    events = []
    for item in data.get("events", []):
        if not isinstance(item, dict) or not item.get("title"):
            continue
        event_type = item.get("event_type") if item.get("event_type") in EVENT_TYPES else "other"
        artists = item.get("artists") or []
        events.append(
            Event(
                title=str(item["title"]).strip(),
                venue=venue.name,
                event_type=event_type,
                start_date=item.get("start_date") or None,
                end_date=item.get("end_date") or None,
                start_time=item.get("start_time") or None,
                end_time=item.get("end_time") or None,
                artists=[str(a) for a in artists if a] if isinstance(artists, list) else [],
                description=item.get("description") or "",
                url=item.get("url") or page_url,
                image_url=item.get("image_url") or "",
                price=item.get("price") if item.get("price") in PRICE_TYPES else "unknown",
                price_note=str(item.get("price_note") or "")[:80],
                source="ai",
            )
        )
    return events
