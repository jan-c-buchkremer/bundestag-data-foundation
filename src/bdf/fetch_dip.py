"""Download DIP API entities for a date range into data/raw/dip/.

Files written (all JSON, each with a .meta.json sidecar):
  drucksache/<start>_<end>.json          all BT Drucksachen dated in the range (merged pages)
  aktivitaet/drucksache-<id>.json        all Aktivitäten linked to one Drucksache (= full author list)
  vorgang/drucksache-<id>.json           all Vorgänge linked to one Drucksache
  vorgangsposition/<start>_<end>.json    all BT Vorgangspositionen dated in the range (vote linking)
  person/wp<wp>.json                     all DIP persons with documents in the Wahlperiode
"""

from datetime import date
from pathlib import Path

import httpx

from bdf import raw
from bdf.config import DIP_BASE_URL, dip_api_key, raw_dir


def dip_dir() -> Path:
    return raw_dir() / "dip"


def _require_key() -> str:
    if not dip_api_key():
        raise SystemExit("DIP_API_KEY is not set (see docs/dip-api-key-request.md)")
    return dip_api_key()


def list_all(http: httpx.Client, endpoint: str, params: dict) -> list[dict]:
    """Follow DIP cursor pagination until the cursor stops changing."""
    key = _require_key()
    url = f"{DIP_BASE_URL}/{endpoint}"
    documents: list[dict] = []
    cursor = None
    while True:
        query = {**params, "format": "json"}
        if cursor:
            query["cursor"] = cursor
        data = raw.get(http, url, params=query, headers={"Authorization": f"ApiKey {key}"}).json()
        documents += data.get("documents", [])
        new_cursor = data.get("cursor")
        if not new_cursor or new_cursor == cursor or not data.get("documents"):
            return documents
        cursor = new_cursor


def _fetch_list(http: httpx.Client, endpoint: str, params: dict, dest: Path, *, force: bool) -> list[dict]:
    url = f"{DIP_BASE_URL}/{endpoint}?" + "&".join(f"{k}={v}" for k, v in params.items())
    return raw.cached_json(dest, url, lambda: list_all(http, endpoint, params), force=force)


def fetch_range(http: httpx.Client, wp: int, start: date, end: date, *, force: bool = False) -> None:
    span = f"{start.isoformat()}_{end.isoformat()}"
    date_params = {
        "f.zuordnung": "BT",
        "f.wahlperiode": wp,
        "f.datum.start": start.isoformat(),
        "f.datum.end": end.isoformat(),
    }
    drucksachen = _fetch_list(http, "drucksache", date_params, dip_dir() / "drucksache" / f"{span}.json", force=force)
    print(f"  drucksachen: {len(drucksachen)}")
    # one aktivitaet + one vorgang call per Drucksache, sequential: a sitting week is a few
    # hundred calls; DIP asks for at most 25 in flight, and we have no key to tune this against yet
    for i, d in enumerate(drucksachen, start=1):
        by_id = {"f.drucksache": d["id"]}
        _fetch_list(http, "aktivitaet", by_id, dip_dir() / "aktivitaet" / f"drucksache-{d['id']}.json", force=force)
        _fetch_list(http, "vorgang", by_id, dip_dir() / "vorgang" / f"drucksache-{d['id']}.json", force=force)
        if i % 50 == 0:
            print(f"  authors/vorgänge: {i}/{len(drucksachen)}")
    positions = _fetch_list(
        http, "vorgangsposition", date_params, dip_dir() / "vorgangsposition" / f"{span}.json", force=force
    )
    print(f"  vorgangspositionen: {len(positions)}")
    persons = _fetch_list(http, "person", {"f.wahlperiode": wp}, dip_dir() / "person" / f"wp{wp}.json", force=True)
    print(f"  persons (WP {wp}): {len(persons)}")
