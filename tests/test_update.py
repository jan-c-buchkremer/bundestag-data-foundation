"""bdf update: window derivation from data/raw and the fetch → ingest orchestration, without network."""

from datetime import date, timedelta

import httpx
import pytest

from bdf import fetch_aw, fetch_bundestag, fetch_dip, fetch_hib, fetch_wikidata, queries, raw, update

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
    monkeypatch.setattr(fetch_bundestag, "refetch_preliminary", lambda http, wp, skip=(): [])
    monkeypatch.setattr(update.fetch_wahl, "fetch_election", lambda http, name: [])
    monkeypatch.setattr(update.fetch_wahl, "fetch_successors", lambda http, name: None)
    monkeypatch.setattr(fetch_bundestag, "fetch_votes", lambda http, s, e: calls.append(("votes", s, e)) or [])
    monkeypatch.setattr(fetch_aw, "fetch_wahlperiode", lambda http, wp, force: calls.append("aw"))
    monkeypatch.setattr(fetch_bundestag, "fetch_biografien", lambda http: calls.append("photos") or [])
    monkeypatch.setattr(fetch_wikidata, "fetch_government", lambda http: calls.append("government") or [])
    monkeypatch.setattr(fetch_hib, "fetch", lambda http, since: calls.append(("hib", since)) or [])
    return calls


def test_run_without_dip_key_skips_dip_and_ingests(data_dir, offline, monkeypatch):
    monkeypatch.delenv("DIP_API_KEY", raising=False)
    monkeypatch.setattr(fetch_dip, "fetch_range", lambda *a, **k: pytest.fail("DIP called without a key"))
    assert update.run(21, TODAY) == 0
    assert offline == [
        "stammdaten", "protocols", ("votes", date(2026, 6, 26), TODAY), "aw", "photos", "government",
        ("hib", date(2025, 3, 25)),
    ]  # fmt: skip
    assert (data_dir / "bundestag.sqlite").exists()


def test_run_reports_dip_block_but_still_ingests(data_dir, offline, monkeypatch):
    monkeypatch.setenv("DIP_API_KEY", "test")

    def blocked(*a, **k):
        raise SystemExit("DIP blocked this IP")

    monkeypatch.setattr(fetch_dip, "fetch_range", blocked)
    assert update.run(21, TODAY) == 1
    assert (data_dir / "bundestag.sqlite").exists()


def test_run_skips_an_unreachable_source_and_still_fetches_the_rest(data_dir, offline, monkeypatch):
    monkeypatch.setenv("DIP_API_KEY", "test")

    def disconnected(*a, **k):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    monkeypatch.setattr(fetch_aw, "fetch_wahlperiode", disconnected)
    monkeypatch.setattr(fetch_dip, "fetch_range", lambda *a, **k: offline.append("dip"))
    assert update.run(21, TODAY) == 1
    assert offline[-1] == "dip"  # the source after the failed one still ran
    assert (data_dir / "bundestag.sqlite").exists()


def _client(responses):
    """An httpx client that answers from ``responses`` in order; an exception instance is raised instead."""
    queue = list(responses)

    def handler(request):
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_retries_a_dropped_connection(monkeypatch):
    monkeypatch.setattr(raw.time, "sleep", lambda s: None)
    dropped = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    http = _client([dropped, httpx.Response(503), httpx.Response(200, text="ok")])
    assert raw.get(http, "https://example.org/x").text == "ok"


def test_get_raises_after_the_last_retry(monkeypatch):
    monkeypatch.setattr(raw.time, "sleep", lambda s: None)
    dropped = httpx.ConnectError("connection refused")
    with pytest.raises(httpx.ConnectError):
        raw.get(_client([dropped] * 4), "https://example.org/x")
    with pytest.raises(httpx.HTTPStatusError):
        raw.get(_client([httpx.Response(502)] * 4), "https://example.org/x")


def test_run_warns_about_stale_protocol_roles_without_failing(data_dir, offline, monkeypatch, capsys):
    monkeypatch.delenv("DIP_API_KEY", raising=False)
    stale = {"name": "W", "person_id": "999990154", "office": "Staatsminister beim Bundeskanzler",
             "to_date": "2026-05-07", "days_since_seen": 139}  # fmt: skip
    monkeypatch.setattr(queries, "stale_roles", lambda conn: [stale])
    assert update.run(21, TODAY) == 0
    out = capsys.readouterr().out
    assert "update: warning: 1 protocol-only government roles not seen in a protocol for more than 90 days" in out
    assert "W (999990154), Staatsminister beim Bundeskanzler: last seen 2026-05-07, 139 days" in out


def test_run_fails_when_the_health_check_does(data_dir, offline, monkeypatch, capsys):
    monkeypatch.delenv("DIP_API_KEY", raising=False)
    assert update.run(21, TODAY) == 0  # the first snapshot
    later = TODAY + timedelta(days=1)
    monkeypatch.setattr(update.ingest, "ingest_all", lambda conn: conn.execute("DELETE FROM speech_paragraph"))
    assert update.run(21, later) == 1
    assert "the health check failed: table speech_paragraph is empty" in capsys.readouterr().out
