"""Download DIP API entities for a date range into data/raw/dip/.

Files written (all JSON, each with a .meta.json sidecar):
  drucksache/<start>_<end>.json                 all BT Drucksachen dated in the range (merged pages)
  aktivitaet/drucksache-<id>.json               all Aktivitäten linked to one Drucksache (= full author list)
  vorgang/drucksache-<id>.json                  all Vorgänge linked to one Drucksache
  vorgangsposition/<start>_<end>.json           all BT Vorgangspositionen dated in the range (vote linking)
  vorgangsposition_other/<start>_<end>-<z>.json BR/BV/EK Vorgangspositionen dated in the range, one file per
                                                 zuordnung `<z>` (Bundesrat steps: 1./2. Durchgang, Zustimmung,
                                                 Einspruch, Vermittlungsausschuss, …); kept separate from the BT
                                                 files so a first backfill of this new data does not get skipped
                                                 as "already cached"
  person/wp<wp>.json                            all DIP persons with documents in the Wahlperiode
"""

import time
from datetime import date
from pathlib import Path

import httpx

from bdf import raw
from bdf.config import DIP_BASE_URL, dip_api_key, raw_dir

# ~14 requests/s got the IP blocked by DIP's bot protection (Enodia); 2/s has not
DIP_REQUEST_INTERVAL = 0.5

# f.zuordnung values other than BT (DIP's Zuordnung enum): Bundesrat, Bundesversammlung, Europakammer.
# Bundesrat steps of a Gesetzgebungsvorgang (1./2. Durchgang, Zustimmung, Einspruch, Zustimmung versagt,
# Anrufung/Beschluss des Vermittlungsausschusses, …) are zuordnung=BR; BV and EK are fetched too because
# the extra request is cheap and the Zuordnung filter takes one value at a time.
OTHER_ZUORDNUNG = ("BR", "BV", "EK")


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
        time.sleep(DIP_REQUEST_INTERVAL)
        response = raw.get(http, url, params=query, headers={"Authorization": f"ApiKey {key}"})
        if response.url.path.startswith("/.enodia/"):
            # a request burst gets a JavaScript proof-of-work challenge instead of JSON, and
            # the IP stays blocked for ~15 minutes; the only remedy is to wait
            raise SystemExit(
                f"DIP blocked this IP after too many requests (Enodia challenge). "
                f"Wait a while and rerun; the fetch resumes from {raw_dir()}. URL: {response.url}"
            )
        data = response.json()
        documents += data.get("documents", [])
        new_cursor = data.get("cursor")
        if not new_cursor or new_cursor == cursor or not data.get("documents"):
            return documents
        cursor = new_cursor


def _fetch_list(http: httpx.Client, endpoint: str, params: dict, dest: Path, *, force: bool) -> list[dict]:
    url = f"{DIP_BASE_URL}/{endpoint}?" + "&".join(f"{k}={v}" for k, v in params.items())
    return raw.cached_json(dest, url, lambda: list_all(http, endpoint, params), force=force)


def _other_zuordnung_start(zuordnung: str, requested_start: date) -> date:
    """The start date to fetch a non-BT `zuordnung` from.

    This data is new: the first call for a given `zuordnung` (no file for it under
    `vorgangsposition_other` yet) backfills from the earliest date any BT vorgangsposition range
    has already been fetched for, since that is the closest thing to "the start of the
    Wahlperiode" this module can read off disk without duplicating `update.WP_START`. Once a
    file exists, later calls use the caller's own (usually much narrower) window.
    """
    if raw.data_files(dip_dir() / "vorgangsposition_other", f"*-{zuordnung}.json"):
        return requested_start
    bt_files = raw.data_files(dip_dir() / "vorgangsposition", "*.json")
    starts = [date.fromisoformat(p.stem.split("_")[0]) for p in bt_files]
    return min(starts, default=requested_start)


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
    # one aktivitaet + one vorgang call per Drucksache, sequential and paced: a sitting week
    # is a few hundred calls, i.e. a few minutes
    for i, d in enumerate(drucksachen, start=1):
        by_id = {"f.drucksache": d["id"]}
        _fetch_list(http, "aktivitaet", by_id, dip_dir() / "aktivitaet" / f"drucksache-{d['id']}.json", force=force)
        _fetch_list(http, "vorgang", by_id, dip_dir() / "vorgang" / f"drucksache-{d['id']}.json", force=force)
        if i % 50 == 0:
            print(f"  authors/vorgänge: {i}/{len(drucksachen)}")
    positions = _fetch_list(
        http, "vorgangsposition", date_params, dip_dir() / "vorgangsposition" / f"{span}.json", force=force
    )
    print(f"  vorgangspositionen (BT): {len(positions)}")
    for zuordnung in OTHER_ZUORDNUNG:
        z_start = _other_zuordnung_start(zuordnung, start)
        z_span = f"{z_start.isoformat()}_{end.isoformat()}"
        z_params = {**date_params, "f.zuordnung": zuordnung, "f.datum.start": z_start.isoformat()}
        z_positions = _fetch_list(
            http,
            "vorgangsposition",
            z_params,
            dip_dir() / "vorgangsposition_other" / f"{z_span}-{zuordnung}.json",
            force=force,
        )
        print(f"  vorgangspositionen ({zuordnung}): {len(z_positions)}")
    persons = _fetch_list(http, "person", {"f.wahlperiode": wp}, dip_dir() / "person" / f"wp{wp}.json", force=True)
    print(f"  persons (WP {wp}): {len(persons)}")
