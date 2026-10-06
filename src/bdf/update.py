"""bdf update: fetch everything new since the last run, then ingest. Meant for an unattended weekly timer.

Each source derives its own window from what is already under data/raw, so the command takes no
dates and a missed week is caught up on the next run. The first run backfills the whole Wahlperiode.
Protocols still in their preliminary version are fetched again on every run until the final one is served;
meanwhile the final PDF of each is fetched too, which supplies the pages the XML lacks (bdf/protocol_pdf.py).
"""

import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import httpx

from bdf import db, fetch_aw, fetch_bundestag, fetch_dip, fetch_wahl, fetch_wikidata, health, ingest, queries, raw
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


def warn_stale_roles(conn: sqlite3.Connection) -> list[dict]:
    """Print a warning per protocol-only government role that no protocol has printed for STALE_AFTER_DAYS before
    the newest sitting. Only a warning: the role stays current and the exit code is unaffected."""
    stale = queries.stale_roles(conn)
    if stale:
        print(
            f"update: warning: {len(stale)} protocol-only government roles not seen in a protocol for more than "
            f"{queries.STALE_AFTER_DAYS} days (kept as current; check with `bdf query stale-roles`):"
        )
        for r in stale:
            seen = f"last seen {r['to_date']}, {r['days_since_seen']} days"
            print(f"    {r['name']} ({r['person_id']}), {r['office']}: {seen}")
    return stale


def run(wp: int = 21, today: date | None = None) -> int:
    """Fetch all sources, then ingest, then check the store's health (bdf/health.py). Returns a process exit code:
    1 if a source had to be skipped or the health check found the store clearly worse than on the last run.

    A source that still fails after `raw.get`'s retries is skipped for this run; the others are fetched
    and everything on disk is ingested, so one unreachable server does not hold back the rest.
    """
    today = today or date.today()
    failed: list[str] = []
    with raw.client() as http:

        def source(name: str, fetch: Callable[[], None]) -> None:
            try:
                fetch()
            except (httpx.HTTPError, SystemExit) as e:  # SystemExit: DIP's Enodia block
                print(f"{name}: failed, skipped this run: {e}")
                failed.append(name)

        def stammdaten() -> None:
            print("stammdaten")
            fetch_bundestag.fetch_stammdaten(http, force=True)  # republished irregularly under the same URL

        def protocols() -> None:
            print(f"protocols from {wp}/{next_protocol(wp)}")
            new = fetch_new_protocols(http, wp)
            for path in new:
                print(f"  {path.name}")
            refetched = fetch_bundestag.refetch_preliminary(http, wp, skip=new)
            if refetched:
                print(f"protocols: {len(refetched)} preliminary ones fetched again")
            for path, still in refetched:
                print(f"  {path.name}: {'still preliminary' if still else 'final version now'}")
            pdfs = fetch_bundestag.fetch_preliminary_pdfs(http, wp)
            if pdfs:
                print(f"protocols: PDFs of the {len(pdfs)} preliminary ones, {sum(c for _, c in pdfs)} new or changed")

        def votes() -> None:
            start, end = votes_window(wp, today)
            print(f"votes {start}..{end}")
            for row in fetch_bundestag.fetch_votes(http, start, end):
                print(f"  {row['date']} #{row['number']} {row['title']}")

        def election() -> None:
            name = fetch_wahl.ELECTION_OF_WAHLPERIODE[wp]
            print(f"wahl {name}")
            fetch_wahl.fetch_election(http, name)  # published once; downloaded only if missing
            fetch_wahl.fetch_successors(http, name)  # grows during the Wahlperiode

        def abgeordnetenwatch() -> None:
            print(f"abgeordnetenwatch WP {wp}")
            fetch_aw.fetch_wahlperiode(http, wp, force=True)

        def photos() -> None:
            print("photos (bundestag.de biographies)")
            cards = fetch_bundestag.fetch_biografien(http)  # list in full; portraits only when new
            print(f"  {len(cards)} cards")

        def government() -> None:
            print("government (Wikidata)")
            print(f"  {len(fetch_wikidata.fetch_government(http))} result rows")

        def dip() -> None:
            start, end = dip_window(wp, today)
            print(f"dip {start}..{end}")
            fetch_dip.fetch_range(http, wp, start, end)

        def answers() -> None:
            new = fetch_bundestag.fetch_answer_pdfs(http, wp)  # the first run fetches all, about 25 minutes
            print(f"answers: {len(new)} PDFs of answers to Anfragen and of Schriftliche Fragen downloaded")

        source("stammdaten", stammdaten)
        source("protocols", protocols)
        source("votes", votes)
        source("wahl", election)
        source("abgeordnetenwatch", abgeordnetenwatch)
        source("photos", photos)
        source("government", government)
        if dip_api_key():
            source("dip", dip)
            source("answers", answers)  # listed by the DIP data just fetched
        else:
            print("dip: skipped, DIP_API_KEY is not set")

    conn = db.connect(db_path())
    ingest.ingest_all(conn)
    warn_stale_roles(conn)
    report = health.check(conn, today)
    print("\n".join(report.lines))
    if failed:
        print(f"update: ingested, but these sources failed: {', '.join(failed)}")
    if report.failures:
        print(f"update: the health check failed: {'; '.join(report.failures)}")
    return 1 if failed or report.failures else 0
