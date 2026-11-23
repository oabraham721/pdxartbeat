"""Extract opening hours, admission and free days for each venue into venue_details.json.

Venue hours change rarely, so run this occasionally (e.g. monthly), not on every scrape.

Usage:
    .venv/bin/python venue_details.py                  # all active venues
    .venv/bin/python venue_details.py --venue "Froelick"
"""
import argparse
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Tuple
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ai_extract import _get_client
from config import OPENAI_MODEL, PRICE_TYPES, TIMEZONE, VENUE_DETAILS_JSON
from fetch import fetch_html, html_to_text
from models import Venue, load_venues

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("venue_details")
logging.getLogger("httpx").setLevel(logging.WARNING)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
INFO_LINK = re.compile(r"visit|hours|contact|about|admission|plan|info|location", re.I)
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
TODAY_ONLY_HOURS = re.compile(r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun|To)day'?s Hours\b", re.I)

SYSTEM_PROMPT = f"""You read a Portland, Oregon art venue's website and extract visitor information.

Return JSON with exactly these keys:
- "hours": object with keys {DAYS}. Each value is a list of [open, close] pairs in 24h "HH:MM" (usually one pair), or [] if closed that day. Use the regular public hours (not holiday exceptions or one-off event times). "Daily" or "Open every day" means all seven days.
- "by_appointment": true if the venue says it is open by appointment (in addition to or instead of regular hours)
- "hours_note": short note about hours exceptions (e.g. "Closed holidays", "Open First Thursday until 8pm"), or ""
- "admission": one of {PRICE_TYPES} for general entry ("free" for commercial galleries that don't mention admission)
- "admission_note": short admission detail (e.g. "$25 adults, under 17 free"), or ""
- "free_days": list of recurring or dated days when admission is free for EVERYONE at venues that normally charge admission (e.g. "Free First Thursdays"). Do NOT include discounts or eligibility programs (members, SNAP/EBT, military, students, kids) — put those in admission_note instead. Each item:
  {{"description": short text, "rule": one of
      {{"type": "monthly_nth_weekday", "nth": 1-5 or -1 for last, "weekday": one of {DAYS}}},
      {{"type": "weekly", "weekday": one of {DAYS}}},
      {{"type": "date", "date": "YYYY-MM-DD"}},
    "start_time": "HH:MM" or null, "end_time": "HH:MM" or null}}
  Return [] if there are none or the venue is always free.

If the pages don't state hours, return every day as [] and add "Hours not listed" to hours_note. If they only show a single day's hours (e.g. "Today's hours" or "Sunday's Hours") without a weekly schedule, return every day as [] and set hours_note to "Hours vary — check website". Do not guess."""


def _pages_for(venue: Venue) -> List[Tuple[str, str]]:
    base = venue.website or venue.page_urls[0]
    final_url, html = fetch_html(base)
    pages = [(final_url, html)]
    candidates = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        if INFO_LINK.search(href + " " + text) and not href.startswith(("mailto:", "tel:", "#", "javascript:")):
            url = urljoin(final_url, href).split("#")[0]
            if url not in {final_url, *candidates} and urlparse_host(url) == urlparse_host(final_url):
                candidates.append(url)
    for url in candidates[:4]:
        try:
            pages.append(fetch_html(url))
        except Exception as exc:
            logger.debug("%s: skip %s (%s)", venue.name, url, exc)
    return pages


def urlparse_host(url: str) -> str:
    return re.sub(r"^www\.", "", url.split("/")[2]) if "//" in url else ""


def _head_and_tail(text: str, limit: int = 7000) -> str:
    # Hours and admission usually live in the site footer, so keep the end of long pages too.
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...\n" + text[-limit // 2:]


def _clean(data: dict) -> dict:
    hours = {}
    for day in DAYS:
        spans = data.get("hours", {}).get(day) or []
        # The model sometimes returns a single flat pair ["11:00", "17:00"] instead of [["11:00", "17:00"]].
        if len(spans) == 2 and all(isinstance(t, str) for t in spans):
            spans = [spans]
        hours[day] = [s for s in spans if isinstance(s, list) and len(s) == 2 and all(isinstance(t, str) and TIME_RE.match(t) for t in s)]
    free_days = []
    for fd in data.get("free_days") or []:
        rule = fd.get("rule") if isinstance(fd, dict) else None
        if isinstance(rule, dict) and rule.get("type") in {"monthly_nth_weekday", "weekly", "date"}:
            free_days.append({
                "description": str(fd.get("description", ""))[:120],
                "rule": rule,
                "start_time": fd.get("start_time") if TIME_RE.match(str(fd.get("start_time"))) else None,
                "end_time": fd.get("end_time") if TIME_RE.match(str(fd.get("end_time"))) else None,
            })
    return {
        "hours": hours,
        "by_appointment": bool(data.get("by_appointment")),
        "hours_note": str(data.get("hours_note") or "")[:120],
        "admission": data.get("admission") if data.get("admission") in PRICE_TYPES else "unknown",
        "admission_note": str(data.get("admission_note") or "")[:120],
        "free_days": free_days,
    }


def extract(venue: Venue) -> Tuple[Venue, dict, str]:
    if not venue.website or "instagram.com" in venue.website:
        return venue, {}, "no website"
    try:
        pages = _pages_for(venue)
        text = "\n\n".join(f"=== PAGE: {u} ===\n{_head_and_tail(html_to_text(h, u))}" for u, h in pages)
        resp = _get_client().chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Venue: {venue.name} ({venue.type})\n\n{text}"},
            ],
        )
        details = _clean(json.loads(resp.choices[0].message.content or "{}"))
        open_days = [d for d in DAYS if details["hours"][d]]
        if len(open_days) == 1 and TODAY_ONLY_HOURS.search(text):
            # Site only shows "Sunday's Hours"-style widgets; a single day is not the weekly schedule.
            details["hours"] = {d: [] for d in DAYS}
            details["hours_note"] = "Hours vary — check website"
        details["sources"] = [u for u, _ in pages]
        return venue, details, ""
    except Exception as exc:
        return venue, {}, f"{type(exc).__name__}: {str(exc)[:120]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--venue", help="only run venues whose name contains this text")
    parser.add_argument("--missing-hours", action="store_true", help="only re-run venues with no hours found yet")
    args = parser.parse_args()

    venues = load_venues()
    if args.venue:
        venues = [v for v in venues if args.venue.lower() in v.name.lower()]

    existing = json.loads(VENUE_DETAILS_JSON.read_text()) if VENUE_DETAILS_JSON.exists() else {}
    if args.missing_hours:
        venues = [v for v in venues if not any(existing.get(v.name, {}).get("hours", {}).values())]
    checked_at = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
    with ThreadPoolExecutor(max_workers=6) as pool:
        for venue, details, error in pool.map(extract, venues):
            if error:
                logger.warning("%-45s %s", venue.name, error)
                continue
            details["checked_at"] = checked_at
            existing[venue.name] = details
            open_days = [d for d in DAYS if details["hours"][d]]
            logger.info("%-45s open %s | %s | %d free days", venue.name, ",".join(open_days) or "-", details["admission"], len(details["free_days"]))

    VENUE_DETAILS_JSON.write_text(json.dumps(existing, indent=1, ensure_ascii=False))
    logger.info("Wrote %d venues to %s", len(existing), VENUE_DETAILS_JSON)


if __name__ == "__main__":
    main()
