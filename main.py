"""Collect current and upcoming art events from Portland galleries into events.json.

Usage:
    .venv/bin/python main.py                    # all active venues
    .venv/bin/python main.py --venue "Blue Sky" # only venues whose name contains this text
"""
import argparse
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Tuple
from zoneinfo import ZoneInfo

from ai_extract import extract_events
from config import ARCHIVE_JSON, EVENTS_JSON, MAX_PAGE_CHARS, TIMEZONE, VENUE_DETAILS_JSON
from fetch import fetch_html, find_ics_link, html_to_text, is_image
from ics_source import events_from_ics
from models import Event, Venue, event_id, load_venues

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("art_attendor")
logging.getLogger("httpx").setLevel(logging.WARNING)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_KEEP_RATIO = 0.5
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
# Retail promotions the AI sometimes lists as events despite being told not to.
SHOP_PROMO_RE = re.compile(r"\b(museum store|gift shop|holiday shop|store sunday|shop sale)\b", re.I)


GENERIC_TITLE_RE = re.compile(r"^(opening|closing)?\s*(reception|party|night)?$|^(first|last|second)\s+(thursday|friday|saturday)$|^artist\s+talk$|^open\s+house$", re.I)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def fix_generic_titles(events: List[Event]) -> None:
    """Give bare titles like "Opening Reception" the show they belong to (or the venue) so listings make sense on their own."""
    for e in events:
        if e.event_type == "exhibition" or not GENERIC_TITLE_RE.match(e.title.strip()):
            continue
        show = next((x for x in events if x.venue == e.venue and x.event_type == "exhibition" and x.start_date and x.start_date == e.start_date), None)
        e.title = f"{e.title.strip()}: {show.title}" if show else f"{e.title.strip()} at {e.venue}"
        if show and not e.artists:
            e.artists = list(show.artists)


def drop_gallery_hours_as_times(events: List[Event], details: dict) -> None:
    """The AI sometimes reports a venue's regular hours as an event's time; a time that exactly matches that day's hours is cleared."""
    for e in events:
        if e.event_type == "exhibition" or not (e.start_date and e.start_time):
            continue
        day = WEEKDAYS[datetime.fromisoformat(e.start_date).weekday()]
        for open_t, close_t in details.get(e.venue, {}).get("hours", {}).get(day, []):
            if e.start_time == open_t and (not e.end_time or e.end_time == close_t):
                e.start_time = e.end_time = None
                break


def apply_venue_details(events: List[Event]) -> None:
    """Use venue_details.json to clean event times and correct or fill event prices."""
    if not VENUE_DETAILS_JSON.exists():
        return
    details = json.loads(VENUE_DETAILS_JSON.read_text())
    drop_gallery_hours_as_times(events, details)
    for e in events:
        admission = details.get(e.venue, {}).get("admission", "unknown")
        if e.price == "unknown" and admission != "unknown":
            e.price = admission
        # "Free" with no stated reason at a paid museum usually means "included with admission".
        elif e.price == "free" and not e.price_note and admission == "ticketed":
            e.price = "ticketed"


def _guess_type(title: str) -> str:
    t = title.lower()
    if "closing" in t and "reception" in t:
        return "closing_reception"
    if "opening" in t or "reception" in t:
        return "opening_reception"
    if "talk" in t or "conversation" in t or "lecture" in t:
        return "artist_talk"
    if "workshop" in t or "class" in t:
        return "workshop"
    return "other"


def collect_venue(venue: Venue, today: str) -> Tuple[Venue, List[Event], str]:
    """Returns (venue, events, error_message)."""
    urls = [u for u in venue.page_urls if "instagram.com" not in u]
    if not urls:
        return venue, [], "skipped: no scrapable website"
    try:
        pages = []  # (final_url, html)
        for url in urls:
            try:
                pages.append(fetch_html(url))
            except Exception as exc:
                logger.warning("%s: could not fetch %s (%s)", venue.name, url, exc)
        if not pages:
            return venue, [], "all pages failed to load"

        if venue.feed == "ics":
            ics_url = next((link for link in (find_ics_link(h, u) for u, h in pages) if link), None)
            if ics_url:
                try:
                    events = events_from_ics(ics_url, venue)
                    for e in events:
                        e.event_type = _guess_type(e.title)
                    if events:
                        return venue, events, ""
                except Exception as exc:
                    logger.warning("%s: ICS failed (%s), falling back to AI", venue.name, exc)

        per_page = MAX_PAGE_CHARS // len(pages)
        texts = [(u, html_to_text(h, u)) for u, h in pages]
        if sum(len(t) for _, t in texts) < 80:
            return venue, [], "page has almost no text (likely JavaScript-rendered)"
        text = "\n\n".join(f"=== PAGE: {u} ===\n{t[:per_page]}" for u, t in texts)
        return venue, extract_events(text, pages[0][0], venue, today), ""
    except Exception as exc:
        return venue, [], f"{type(exc).__name__}: {str(exc)[:120]}"


def clean(events: List[Event], today: str) -> List[Event]:
    seen = set()
    out = []
    for e in events:
        for attr, pattern in (("start_date", DATE_RE), ("end_date", DATE_RE), ("start_time", TIME_RE), ("end_time", TIME_RE)):
            value = getattr(e, attr)
            if value and not pattern.match(value):
                setattr(e, attr, None)
        if e.start_date and e.end_date and e.end_date < e.start_date:
            e.end_date = None

        last_day = e.end_date or e.start_date
        if last_day and last_day < today:
            continue
        if SHOP_PROMO_RE.search(e.title):
            continue

        key = (e.venue, re.sub(r"\W+", "", e.title.lower()), e.event_type, e.start_date)
        if key in seen:
            continue
        seen.add(key)
        e.id = event_id(e.venue, e.title, e.event_type, e.start_date)
        out.append(e)

    out.sort(key=lambda e: (e.start_date or "9999", e.venue, e.title))
    return out


def drop_broken_images(events: List[Event]) -> int:
    """Blank out image URLs that don't load as images, so the site shows a placeholder instead of an empty box."""
    urls = sorted({e.image_url for e in events if e.image_url})
    with ThreadPoolExecutor(max_workers=8) as pool:
        good = {u for u, fine in zip(urls, pool.map(is_image, urls)) if fine}
    broken = 0
    for e in events:
        if e.image_url and e.image_url not in good:
            e.image_url = ""
            broken += 1
    return broken


def _load_events_file() -> List[Event]:
    if not EVENTS_JSON.exists():
        return []
    fields = set(Event.__dataclass_fields__)
    return [Event(**{k: v for k, v in d.items() if k in fields}) for d in json.loads(EVENTS_JSON.read_text())["events"]]


def update_archive(events: List[Event], today: str) -> int:
    """Keep every event ever seen (keyed by id) so artist and venue pages can show past shows."""
    archive = json.loads(ARCHIVE_JSON.read_text()) if ARCHIVE_JSON.exists() else {}
    for e in events:
        entry = archive.get(e.id, {})
        archive[e.id] = {**e.to_dict(), "first_seen": entry.get("first_seen", today), "last_seen": today}
    ARCHIVE_JSON.write_text(json.dumps(archive, indent=1, ensure_ascii=False))
    return len(archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--venue", help="only run venues whose name contains this text (case-insensitive)")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    today = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
    venues = load_venues()
    if args.venue:
        venues = [v for v in venues if args.venue.lower() in v.name.lower()]
    if not venues:
        parser.error("no matching venues")

    logger.info("Collecting events from %d venues (today=%s)", len(venues), today)
    all_events: List[Event] = []
    report = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for venue, events, error in pool.map(lambda v: collect_venue(v, today), venues):
            for e in events:
                e.neighborhood = venue.neighborhood
                e.address = venue.address
            all_events.extend(events)
            report.append({"venue": venue.name, "events_found": len(events), "error": error})
            if error:
                logger.warning("%-45s %s", venue.name, error)
            else:
                logger.info("%-45s %d events", venue.name, len(events))

    if args.venue:
        # Partial run: keep the other venues' events from the previous run.
        ran = {v.name for v in venues}
        all_events.extend(e for e in _load_events_file() if e.venue not in ran)

    events = clean(all_events, today)
    fix_generic_titles(events)
    apply_venue_details(events)
    logger.info("Dropped %d broken image links", drop_broken_images(events))

    previous = len(_load_events_file())
    if not args.venue and previous >= 20 and len(events) < previous * MIN_KEEP_RATIO:
        # Probably an outage (OpenAI down, network trouble); keep yesterday's data rather than publishing an empty site.
        logger.error("Only %d events vs %d last run; not overwriting %s", len(events), previous, EVENTS_JSON)
        raise SystemExit(1)
    output = {
        "generated_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds"),
        "event_count": len(events),
        "events": [e.to_dict() for e in events],
        "venue_report": sorted(report, key=lambda r: r["venue"]),
    }
    EVENTS_JSON.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    logger.info("Wrote %d events to %s", len(events), EVENTS_JSON)
    logger.info("Archive now holds %d events", update_archive(events, today))


if __name__ == "__main__":
    main()
