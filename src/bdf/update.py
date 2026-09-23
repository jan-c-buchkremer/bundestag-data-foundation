"""bdf update: fetch everything new since the last run, then ingest. Meant for an unattended weekly timer.

Each source derives its own window from what is already under data/raw, so the command takes no
dates and a missed week is caught up on the next run. The first run backfills the whole Wahlperiode.
"""

from datetime import date, timedelta
from pathlib import Path

import httpx

from bdf import db, fetch_aw, fetch_bundestag, fetch_dip, ingest, raw
from bdf.config import db_path, dip_api_key

# constituent sitting of each Wahlperiode: the earliest date any source is asked for
WP_START = {21: date(2025, 3, 25)}
# votes and DIP documents can be published days after their date, so each run re-reads this far back
LOOKBACK = timedelta(days=14)
# protocol numbers are probed upwards until this many in a row are not published
PROTOCOL_MISSES = 3


def next_protocol(wp: int) -> int:
    """The first sitting number after the highest protocol already on disk."""
    prefix = len(str(wp))
    numbers = [int(p.stem[prefix:]) for p in raw.data_files(fetch_bundestag.protocols_dir() / str(wp), "*.xml")]
    return max(numbers, default=0) + 1


def fetch_new_protocols(http: httpx.Client, wp: int) -> list[Path]:
    nr, misses, paths = next_protocol(wp), 0, []
    while misses < PROTOCOL_MISSES:
        got = fetch_bundestag.fetch_protocols(http, wp, nr, nr)
        paths += got
        misses = 0 if got else misses + 1
        nr += 1
    return paths


def votes_window(wp: int, today: date) -> tuple[date, date]:
    index = fetch_bundestag.votes_index_path()
    dates = [date.fromisoformat(r["date"]) for r in raw.read_json(index)] if index.exists() else []
    return _window(wp, max(dates, default=None), today)


def dip_window(wp: int, today: date) -> tuple[date, date]:
    """From the end of the latest fetched Drucksache range (file name `<start>_<end>.json`)."""
    files = raw.data_files(fetch_dip.dip_dir() / "drucksache", "*.json")
    ends = [date.fromisoformat(p.stem.split("_")[1]) for p in files]
    return _window(wp, max(ends, default=None), today)


def _window(wp: int, last: date | None, today: date) -> tuple[date, date]:
    start = WP_START[wp] if last is None else max(WP_START[wp], last - LOOKBACK)
    return start, today


def run(wp: int = 21, today: date | None = None) -> int:
    """Fetch all sources, then ingest. Returns a process exit code: 1 if a source had to be skipped."""
    today = today or date.today()
    failed = False
    with raw.client() as http:
        print("stammdaten")
        fetch_bundestag.fetch_stammdaten(http, force=True)  # republished irregularly under the same URL

        print(f"protocols from {wp}/{next_protocol(wp)}")
        for path in fetch_new_protocols(http, wp):
            print(f"  {path.name}")

        start, end = votes_window(wp, today)
        print(f"votes {start}..{end}")
        for row in fetch_bundestag.fetch_votes(http, start, end):
            print(f"  {row['date']} #{row['number']} {row['title']}")

        print(f"abgeordnetenwatch WP {wp}")
        fetch_aw.fetch_wahlperiode(http, wp, force=True)

        if not dip_api_key():
            print("dip: skipped, DIP_API_KEY is not set")
        else:
            start, end = dip_window(wp, today)
            print(f"dip {start}..{end}")
            try:
                fetch_dip.fetch_range(http, wp, start, end)
            except SystemExit as e:  # Enodia block: ingest what we have, report the failure
                print(f"dip: {e}")
                failed = True

    ingest.ingest_all(db.connect(db_path()))
    return 1 if failed else 0
