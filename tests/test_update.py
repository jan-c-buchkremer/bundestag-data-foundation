"""bdf update: window derivation from data/raw and the fetch → ingest orchestration, without network."""

from datetime import date

import pytest

from bdf import fetch_aw, fetch_bundestag, fetch_dip, update

TODAY = date(2026, 9, 23)


def test_windows_resume_from_raw_with_lookback(data_dir):
    assert update.next_protocol(21) == 95  # fixtures hold 21094.xml
    assert update.votes_window(21, TODAY) == (date(2026, 6, 26), TODAY)  # last vote 2026-07-10
    assert update.dip_window(21, TODAY) == (date(2026, 6, 26), TODAY)  # last range ends 2026-07-10


def test_windows_start_at_wahlperiode_on_empty_raw(tmp_path, monkeypatch):
    monkeypatch.setenv("BDF_DATA_DIR", str(tmp_path))
    assert update.next_protocol(21) == 1
    assert update.votes_window(21, TODAY) == (date(2025, 3, 25), TODAY)
    assert update.dip_window(21, TODAY) == (date(2025, 3, 25), TODAY)


def test_protocol_probing_stops_after_consecutive_misses(data_dir, monkeypatch):
    published = {95, 96, 98}  # 97 not out yet: one miss must not stop the probe
    asked = []

    def fake(http, wp, first, last, *, force=False):
        asked.append(first)
        return [fetch_bundestag.protocol_path(wp, first)] if first in published else []

    monkeypatch.setattr(fetch_bundestag, "fetch_protocols", fake)
    paths = update.fetch_new_protocols(None, 21)
    assert [p.name for p in paths] == ["21095.xml", "21096.xml", "21098.xml"]
    assert asked == [95, 96, 97, 98, 99, 100, 101]


@pytest.fixture
def offline(monkeypatch):
    """Every fetcher replaced by a recorder; ingest runs for real on the fixtures."""
    calls = []
    monkeypatch.setattr(fetch_bundestag, "fetch_stammdaten", lambda http, force: calls.append("stammdaten"))
    monkeypatch.setattr(update, "fetch_new_protocols", lambda http, wp: calls.append("protocols") or [])
    monkeypatch.setattr(fetch_bundestag, "fetch_votes", lambda http, s, e: calls.append(("votes", s, e)) or [])
    monkeypatch.setattr(fetch_aw, "fetch_wahlperiode", lambda http, wp, force: calls.append("aw"))
    return calls


def test_run_without_dip_key_skips_dip_and_ingests(data_dir, offline, monkeypatch):
    monkeypatch.delenv("DIP_API_KEY", raising=False)
    monkeypatch.setattr(fetch_dip, "fetch_range", lambda *a, **k: pytest.fail("DIP called without a key"))
    assert update.run(21, TODAY) == 0
    assert offline == ["stammdaten", "protocols", ("votes", date(2026, 6, 26), TODAY), "aw"]
    assert (data_dir / "bundestag.sqlite").exists()


def test_run_reports_dip_block_but_still_ingests(data_dir, offline, monkeypatch):
    monkeypatch.setenv("DIP_API_KEY", "test")

    def blocked(*a, **k):
        raise SystemExit("DIP blocked this IP")

    monkeypatch.setattr(fetch_dip, "fetch_range", blocked)
    assert update.run(21, TODAY) == 1
    assert (data_dir / "bundestag.sqlite").exists()
