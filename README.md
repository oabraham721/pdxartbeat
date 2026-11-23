# PDX Artbeat

**Live site: https://oabraham721.github.io/pdxartbeat/**

Every gallery opening, exhibition, artist talk and free museum day in Portland, Oregon — collected daily from 35+ galleries, museums and art spaces.

## How it works

A GitHub Actions workflow ([`.github/workflows/update.yml`](.github/workflows/update.yml)) runs daily at ~5 AM Portland time:

1. **`main.py`** scrapes every active venue in `venues.csv`:
   - reads its iCal feed if `feed` is `ics` and a feed link is found, otherwise
   - fetches the page(s) in `events_url` (separated by ` | `), flattens them to text, and asks OpenAI (`gpt-4o-mini`) to extract events.

   Events are then cleaned (bad dates, past events and shop promotions dropped, duplicates removed, generic titles like "Opening Reception" given their show's name, gallery hours mistaken for event times cleared, broken image links removed), given a stable `id`, and priced (`free` / `suggested_donation` / `ticketed`, falling back to the venue's admission). Results go to `events.json`, every event is merged into `archive.json` (never pruned — powers past shows on artist pages), and `events.ics` is written as a subscribable feed.

   If a run finds less than half as many events as the last one (e.g. an OpenAI outage), it exits without overwriting `events.json`.
2. **`venue_details.py`** (1st of each month, or on demand) extracts each venue's weekly hours, admission and free-admission days into `venue_details.json`.
3. **`build_site.py`** writes `site/data.js`, `site/events.ics` and one `site/ics/<id>.ics` per event, and geocodes any new venue into `venue_coords.json`.
4. The bot commits the refreshed data files (so the archive persists between runs) and deploys `site/` to GitHub Pages.

Pushing changes to `site/` or `build_site.py` redeploys the site from the committed data without scraping.

## Setup (local)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then add your OPENAI_API_KEY
```

GitHub Actions needs the `OPENAI_API_KEY` repository secret, and Pages set to deploy from GitHub Actions.

## Run locally

```bash
.venv/bin/python main.py                     # all venues
.venv/bin/python main.py --venue "Blue Sky"  # one venue (keeps other venues' events from the last run)

.venv/bin/python venue_details.py                  # hours/admission/free days for all venues
.venv/bin/python venue_details.py --missing-hours  # only venues with no hours yet

.venv/bin/python build_site.py               # then serve it: cd site && python3 -m http.server
```

To refresh the live site by hand: **Actions → Update events and deploy site → Run workflow** (optionally tick "refresh venue details" or "skip scrape").

## Adding a venue

Add a row to `venues.csv` with `status=active` and the URL(s) of the page(s) that list its exhibitions/events in `events_url`. The next run scrapes it and places it on the map automatically.
