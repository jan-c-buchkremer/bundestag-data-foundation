"""Download abgeordnetenwatch.de mandates and politicians of one Wahlperiode.

Rate limit is 30 requests/minute/IP; a full period is ~7 mandate pages + ~7 politician
pages, so a 2 s pause between calls keeps us well under it.
"""

import json
import time
from pathlib import Path

import httpx

from bdf import raw
from bdf.config import AW_BASE_URL, raw_dir

# abgeordnetenwatch parliament_period id per Bundestag Wahlperiode
PERIOD_BY_WAHLPERIODE = {20: 132, 21: 161}
PAGE = 100
PAUSE_SECONDS = 2.0


def aw_dir() -> Path:
    return raw_dir() / "abgeordnetenwatch"


def mandates_path(wp: int) -> Path:
    return aw_dir() / f"wp{wp}-mandates.json"


def politicians_path(wp: int) -> Path:
    return aw_dir() / f"wp{wp}-politicians.json"


def _list_all(http: httpx.Client, endpoint: str, params: dict) -> list[dict]:
    items: list[dict] = []
    start = 0
    while True:
        query = {**params, "range_start": start, "range_end": PAGE}
        page = raw.get(http, f"{AW_BASE_URL}/{endpoint}", params=query).json()["data"]
        items += page
        if len(page) < PAGE:
            return items
        start += PAGE
        time.sleep(PAUSE_SECONDS)


def _politicians(http: httpx.Client, mandates: list[dict]) -> list[dict]:
    ids = sorted({m["politician"]["id"] for m in mandates})
    politicians: list[dict] = []
    for i in range(0, len(ids), PAGE):
        chunk = json.dumps(ids[i : i + PAGE], separators=(",", ":"))
        query = {"id[in]": chunk, "range_end": PAGE}
        politicians += raw.get(http, f"{AW_BASE_URL}/politicians", params=query).json()["data"]
        time.sleep(PAUSE_SECONDS)
    return politicians


def fetch_wahlperiode(http: httpx.Client, wp: int, *, force: bool = False) -> None:
    period = PERIOD_BY_WAHLPERIODE[wp]
    mandates_url = f"{AW_BASE_URL}/candidacies-mandates?parliament_period={period}&type=mandate"
    mandates = raw.cached_json(
        mandates_path(wp),
        mandates_url,
        lambda: _list_all(http, "candidacies-mandates", {"parliament_period": period, "type": "mandate"}),
        force=force,
    )
    print(f"  mandates: {len(mandates)}")
    politicians = raw.cached_json(
        politicians_path(wp),
        f"{AW_BASE_URL}/politicians?id[in]=<mandate holders of period {period}>",
        lambda: _politicians(http, mandates),
        force=force,
    )
    print(f"  politicians: {len(politicians)}")
