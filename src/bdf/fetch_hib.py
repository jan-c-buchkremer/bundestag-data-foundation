"""Download "heute im bundestag" (hib): page through the list behind bundestag.de/presse/hib, newest first, and
fetch every article page once. The list itself is not stored; each article page carries its own date and number."""

import time
from datetime import date
from pathlib import Path

import httpx

from bdf import parse_hib, raw
from bdf.config import raw_dir

LIST_URL = "https://www.bundestag.de/ajax/filterlist/de/presse/hib/454590-454590"
ARTICLE_URL = "https://www.bundestag.de/presse/hib/kurzmeldungen-{id}"
PAGE = 20  # the server returns 20 items per page whatever limit asks for
PAUSE = 1.0  # seconds between requests: about one a second


def hib_dir() -> Path:
    return raw_dir() / "bundestag" / "hib"


def article_path(item_id: str) -> Path:
    return hib_dir() / f"{item_id}.html"


def listed(http: httpx.Client, since: date, *, stop_at_known: bool = True) -> list[str]:
    """Article ids from the list, newest first, down to the first day before `since`. With `stop_at_known`, also stop
    after a page whose items are all on disk already (the daily run)."""
    ids: list[str] = []
    offset = 0
    while True:
        rows = parse_hib.list_page(raw.get(http, LIST_URL, params={"offset": offset}).text)
        if not rows:
            break
        page = [i for i, day in rows if day is None or date.fromisoformat(day) >= since]
        ids += [i for i in page if i not in ids]
        days = [date.fromisoformat(d) for _, d in rows if d]
        if (days and min(days) < since) or (stop_at_known and all(article_path(i).exists() for i, _ in rows)):
            break
        offset += len(rows)
        time.sleep(PAUSE)
    return ids


def fetch(http: httpx.Client, since: date, *, stop_at_known: bool = True, force: bool = False) -> list[Path]:
    """Download every listed article not on disk yet; returns the new files. An article that is gone (404) is
    skipped with a note."""
    new: list[Path] = []
    for item_id in listed(http, since, stop_at_known=stop_at_known and not force):
        path = article_path(item_id)
        if path.exists() and not force:
            continue
        try:
            raw.download(http, ARTICLE_URL.format(id=item_id), path, force=force)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            print(f"hib: {item_id} not found, skipped")
            continue
        new.append(path)
        if len(new) % 200 == 0:
            print(f"hib: {len(new)} articles downloaded")
        time.sleep(PAUSE)
    return new
