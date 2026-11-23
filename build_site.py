"""Build the static site's data into site/: data.js, events.ics (subscribable feed) and ics/<id>.ics (one file per event).

Also geocodes any venue missing from venue_coords.json (OpenStreetMap Nominatim, cached).

Usage:
    .venv/bin/python build_site.py
"""
import json
import logging
import shutil
import time
from pathlib import Path

import requests

from config import ARCHIVE_JSON, BASE_DIR, EVENTS_ICS, EVENTS_JSON, VENUE_DETAILS_JSON
from ics_export import write_event_ics
from models import Event, load_venues

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_site")

SITE_DIR = BASE_DIR / "site"
COORDS_JSON = BASE_DIR / "venue_coords.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def geocode_missing(venues) -> dict:
    coords = _load(COORDS_JSON, {})
    missing = [v for v in venues if v.name not in coords]
    for v in missing:
        query = f"{v.address.split(',')[0]}, Portland, OR" if v.address else f"{v.name}, Portland, OR"
        try:
            # Nominatim policy: identify the app and stay under 1 request/second.
            res = requests.get(NOMINATIM, params={"q": query, "format": "json", "limit": 1},
                               headers={"User-Agent": "pdx-artbeat/1.0 (https://github.com/oabraham721/pdxartbeat)"}, timeout=15).json()
            if res:
                coords[v.name] = [float(res[0]["lat"]), float(res[0]["lon"])]
                logger.info("Geocoded %s", v.name)
            else:
                logger.warning("Could not geocode %s (%s)", v.name, query)
        except Exception as exc:
            logger.warning("Geocoding failed for %s: %s", v.name, exc)
        time.sleep(1.1)
    if missing:
        COORDS_JSON.write_text(json.dumps(coords, indent=1, ensure_ascii=False))
    return coords


def main():
    data = json.loads(EVENTS_JSON.read_text())
    venues = load_venues()
    current_ids = {e["id"] for e in data["events"]}
    archive = _load(ARCHIVE_JSON, {})
    today = data["generated_at"][:10]
    # Only events whose dates have actually passed count as "past". Entries that merely vanished from a
    # venue's page (or were re-titled by the AI, which changes their id) would otherwise show as past shows.
    past = [e for eid, e in archive.items()
            if eid not in current_ids and (e.get("end_date") or e.get("start_date") or "9999") < today]

    bundle = {
        "generated_at": data["generated_at"],
        "events": data["events"],
        "past_events": past,
        "venues": [v.__dict__ for v in venues],
        "details": _load(VENUE_DETAILS_JSON, {}),
        "coords": geocode_missing(venues),
    }
    (SITE_DIR / "data.js").write_text("window.ART_DATA = " + json.dumps(bundle, ensure_ascii=False) + ";\n")

    if EVENTS_ICS.exists():
        shutil.copy(EVENTS_ICS, SITE_DIR / "events.ics")

    # One downloadable .ics per event: more reliable than data: URLs, which some browsers block or open as text.
    ics_dir = SITE_DIR / "ics"
    shutil.rmtree(ics_dir, ignore_errors=True)
    ics_dir.mkdir()
    fields = set(Event.__dataclass_fields__)
    written = sum(write_event_ics(Event(**{k: v for k, v in e.items() if k in fields}), ics_dir / f"{e['id']}.ics") for e in data["events"])

    logger.info("Built site: %d events, %d archived, %d venue details, %d per-event calendar files",
                len(data["events"]), len(past), len(bundle["details"]), written)


if __name__ == "__main__":
    main()
