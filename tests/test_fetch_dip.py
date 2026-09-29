"""fetch_dip: the BR/BV/EK Vorgangsposition backfill added alongside the BT range fetch."""

import json
from datetime import date

import httpx
import pytest

from bdf import fetch_dip, raw
from bdf.config import dip_api_key


def _client(responses):
    """An httpx client that answers ``responses`` in call order (see tests/test_update.py)."""
    queue = list(responses)

    def handler(request):
        return queue.pop(0)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _page(documents):
    return httpx.Response(200, json={"documents": documents, "cursor": None})


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_dip.time, "sleep", lambda s: None)
    monkeypatch.setenv("BDF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DIP_API_KEY", "test-key")
    assert dip_api_key() == "test-key"


def _seed_bt_file(start: str, end: str) -> None:
    """A BT vorgangsposition range file already on disk, as a real store has after earlier runs."""
    dest = fetch_dip.dip_dir() / "vorgangsposition" / f"{start}_{end}.json"
    raw.write_json(dest, "https://search.dip.bundestag.de/api/v1/vorgangsposition?seed", [])


def test_other_zuordnung_start_falls_back_to_earliest_bt_range():
    # nothing on disk at all: no BT range to fall back to, so the caller's own window is used
    assert fetch_dip._other_zuordnung_start("BR", date(2026, 9, 1)) == date(2026, 9, 1)
    _seed_bt_file("2025-03-25", "2025-04-01")
    _seed_bt_file("2026-08-01", "2026-08-31")
    # BR was never fetched: backfill from the earliest BT range already on disk, not the narrow
    # incremental window a caller like `update.dip()` would otherwise pass
    assert fetch_dip._other_zuordnung_start("BR", date(2026, 9, 1)) == date(2025, 3, 25)


def test_other_zuordnung_start_uses_requested_start_once_backfilled():
    _seed_bt_file("2025-03-25", "2025-04-01")
    other = fetch_dip.dip_dir() / "vorgangsposition_other"
    raw.write_json(other / "2025-03-25_2026-09-01-BR.json", "https://…", [])
    assert fetch_dip._other_zuordnung_start("BR", date(2026, 9, 1)) == date(2026, 9, 1)
    # a different zuordnung without its own file still backfills
    assert fetch_dip._other_zuordnung_start("BV", date(2026, 9, 1)) == date(2025, 3, 25)


def test_fetch_range_backfills_br_bv_ek_once_then_narrows(capsys):
    _seed_bt_file("2025-03-25", "2025-08-01")  # this store has BT data back to the Wahlperiode start
    http = _client(
        [
            _page([]),  # drucksache: none in this narrow window, skips the per-Drucksache loop
            _page([{"id": "1", "vorgangsposition": "2. Beratung", "zuordnung": "BT", "vorgang_id": "9"}]),
            _page([{"id": "2", "vorgangsposition": "Zustimmung", "zuordnung": "BR", "vorgang_id": "9"}]),
            _page([]),  # BV
            _page([]),  # EK
            _page([]),  # person
        ]
    )
    fetch_dip.fetch_range(http, 21, date(2026, 9, 1), date(2026, 9, 29))
    other = fetch_dip.dip_dir() / "vorgangsposition_other"
    br_file = other / "2025-03-25_2026-09-29-BR.json"
    assert json.loads(br_file.read_text())[0]["vorgangsposition"] == "Zustimmung"
    assert json.loads(raw.meta_path(br_file).read_text())["url"].startswith(
        "https://search.dip.bundestag.de/api/v1/vorgangsposition?f.zuordnung=BR"
    )
    assert (other / "2025-03-25_2026-09-29-BV.json").exists()
    assert (other / "2025-03-25_2026-09-29-EK.json").exists()
    out = capsys.readouterr().out
    assert "vorgangspositionen (BT): 1" in out
    assert "vorgangspositionen (BR): 1" in out

    # a second, later call: BR/BV/EK now have a file each, so the window narrows to the caller's own
    http2 = _client([_page([]), _page([]), _page([]), _page([]), _page([]), _page([])])
    fetch_dip.fetch_range(http2, 21, date(2026, 9, 22), date(2026, 10, 6))
    assert (other / "2026-09-22_2026-10-06-BR.json").exists()


def test_fetch_range_reuses_cached_other_zuordnung_files(capsys):
    """`force=False`: a range already fetched for BR/BV/EK is not requested again.

    The BT range on disk starts on the same day as this call's window, so the backfill in
    `_other_zuordnung_start` has nothing earlier to reach for and the BR/BV/EK span this call
    would ask for is exactly the one already cached below.
    """
    _seed_bt_file("2026-09-01", "2026-09-15")
    other = fetch_dip.dip_dir() / "vorgangsposition_other"
    for z in fetch_dip.OTHER_ZUORDNUNG:
        raw.write_json(other / f"2026-09-01_2026-09-29-{z}.json", "https://…cached", [{"id": f"cached-{z}"}])
    http = _client(
        [
            _page([]),  # drucksache
            _page([]),  # vorgangsposition BT
            _page([]),  # person
        ]
    )
    fetch_dip.fetch_range(http, 21, date(2026, 9, 1), date(2026, 9, 29))
    for z in fetch_dip.OTHER_ZUORDNUNG:
        cached = json.loads((other / f"2026-09-01_2026-09-29-{z}.json").read_text())
        assert cached == [{"id": f"cached-{z}"}]  # untouched: no HTTP call was made for it
