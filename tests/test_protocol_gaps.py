"""Preliminary protocols: detection, the sitting flags, re-fetching, the DIP text fallback and the gap query.

The preliminary and final variants of 21/96 are built here from the real excerpt in ``sub_items/`` by adding
page markers, the preliminary note and (final variant) one more agenda item. The note is worded as the Bundestag
prints it ("Der gesamte und damit endgültige Stenografische Bericht … wird am … veröffentlicht"); its position in
the file is an assumption, which is why detection searches the whole text. The DIP text is synthetic too.
"""

import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

from bdf import db, fetch_bundestag, fetch_dip, ingest, protocol_status, queries, raw, update

FIXTURES = Path(__file__).parent / "fixtures"
EXCERPT = (FIXTURES / "sub_items" / "21096.xml").read_text(encoding="utf-8")
NOTE = "Der gesamte und damit endgültige Stenografische Bericht der 96. Sitzung wird am 2. Oktober 2026 veröffentlicht."


def _marker(page: int) -> str:
    return f'<a id="S{page}" name="S{page}" typ="druckseitennummer" />'


def protocol_96(*, final: bool) -> str:
    """21/96 excerpt with page markers 11785–11790; preliminary: plus the note; final: plus pages to 11798 and a
    Tagesordnungspunkt 12 on Drucksache 21/9999 at the end (the debate the preliminary version lacks)."""
    xml = EXCERPT.replace("<sitzungsverlauf>", f"<sitzungsverlauf>\n        {_marker(11785)}", 1)
    xml = xml.replace('<tagesordnungspunkt top-id="Tagesordnungspunkt 8">',
                      f'{_marker(11786)}\n        <tagesordnungspunkt top-id="Tagesordnungspunkt 8">', 1)  # fmt: skip
    tail = f"        {_marker(11790)}\n"
    if final:
        tail += (
            f'        {_marker(11795)}\n        <tagesordnungspunkt top-id="Tagesordnungspunkt 12">\n'
            '            <p klasse="T_NaS">Erste Beratung des Entwurfs eines Gesetzes über Lotsen</p>\n'
            '            <p klasse="T_Drs">Drucksache 21/9999</p>\n'
            f"        </tagesordnungspunkt>\n        {_marker(11798)}\n"
        )
    else:
        tail += f'        <p klasse="J">{NOTE}</p>\n'
    return xml.replace("    </sitzungsverlauf>", tail + "    </sitzungsverlauf>", 1)


def dip_text() -> str:
    """Plain text as DIP makes it from the PDF: title page, then pages with the running header, the number
    before it on left pages and after it on right pages. "21/1234" before a header must not be read as a page."""
    head = "Deutscher Bundestag – 21. Wahlperiode – 96. Sitzung. Berlin, Freitag, den 25. September 2026"
    body = ["Plenarprotokoll 21/96 Deutscher Bundestag Stenografischer Bericht 96. Sitzung Inhalt: …"]
    for page in range(11785, 11799):
        header = f"{page} {head}" if page % 2 == 0 else f"{head} {page}"
        body.append(f"{header}\n(A) Text der Seite {page}, Drucksache 21/1234")
    return "\n".join(body)


def _write(path: Path, text: str, url: str, retrieved_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    raw.meta_path(path).write_text(json.dumps({"url": url, "retrieved_at": retrieved_at}), encoding="utf-8")


def _status(xml: str) -> protocol_status.Status:
    return protocol_status.status(ET.fromstring(xml.split("\n", 1)[1]))


# --- detection ----------------------------------------------------------------------------


def test_preliminary_note_and_pages_are_read():
    s = _status(protocol_96(final=False))
    assert s == protocol_status.Status(True, "2026-10-02", 11785, 11790)
    assert _status(protocol_96(final=True)) == protocol_status.Status(False, None, 11785, 11798)


@pytest.mark.parametrize(
    "path", ["bundestag/protocols/21/21094.xml", "decisions/21090.xml", "sub_items/21050.xml", "fragestunde/21013.xml"]
)
def test_real_final_protocols_are_not_preliminary(path):
    s = protocol_status.status(ET.parse(FIXTURES / path).getroot())
    assert not s.preliminary and s.final_announced is None
    assert s.last_page is None  # the excerpts carry no page markers; start-seitennr is still read
    assert s.first_page is not None


def test_preliminary_report_kind_alone_counts():
    xml = EXCERPT.replace("<sitzungsverlauf>", "<sitzungsverlauf><p>Vorläufiger Stenografischer Bericht</p>", 1)
    assert _status(xml) == protocol_status.Status(True, None, 11785, None)


@pytest.mark.parametrize(
    ("text", "iso"),
    [("Freitag, den 2. Oktober 2026", "2026-10-02"), ("02.10.2026", "2026-10-02"), ("12. März 2026", "2026-03-12"),
     ("demnächst", None)],
)  # fmt: skip
def test_german_date(text, iso):
    assert protocol_status.german_date(text) == iso


def test_dip_text_is_cut_at_the_page_headers():
    pages = protocol_status.split_pages(dip_text(), 21, 96, 11785)
    assert sorted(pages) == list(range(11785, 11799))
    assert pages[11795] == "(A) Text der Seite 11795, Drucksache 21/1234"
    assert protocol_status.split_pages("no headers here", 21, 96) == {}
    assert protocol_status.split_pages(dip_text(), 21, 95) == {}  # another sitting's header


def test_a_page_number_out_of_sequence_is_not_a_page():
    text = dip_text().replace("11790 Deutscher", "99 Deutscher")  # a stray number before a header
    pages = protocol_status.split_pages(text, 21, 96, 11785)
    assert 99 not in pages and 11790 not in pages
    assert pages[11791]  # the sequence goes on after the gap


# --- store ---------------------------------------------------------------------------------

PROTOCOL = Path("bundestag/protocols/21/21096.xml")
DIP_TEXT = Path("dip/plenarprotokoll-text/21096.json")
POSITIONS = Path("dip/vorgangsposition/2026-09-25_2026-09-25.json")


def _positions() -> list[dict]:
    """Three Beratungen DIP places in 21/96 and the Drucksachen of their Vorgänge."""

    def pos(pid, vorgang, position, kind, number, pages=None):
        f = {"dokumentart": kind, "dokumentnummer": number}
        if pages:
            f["anfangsseite"], f["endseite"] = pages
        return {"id": pid, "vorgang_id": vorgang, "datum": "2026-09-25", "vorgangsposition": position,
                "zuordnung": "BT", "fundstelle": f}  # fmt: skip

    return [
        pos("p1", "v-matched", "Beschlussempfehlung", "Drucksache", "21/5650"),
        pos("p2", "v-matched", "Beratung", "Plenarprotokoll", "21/96", (11786, 11786)),
        pos("p3", "v-late", "Gesetzentwurf", "Drucksache", "21/9999"),
        pos("p4", "v-late", "1. Beratung", "Plenarprotokoll", "21/96", (11795, 11798)),
        pos("p5", "v-other", "Antrag", "Drucksache", "21/8888"),
        pos("p6", "v-other", "2. Beratung", "Plenarprotokoll", "21/96", (11787, 11788)),
        pos("p7", "v-other", "Mitteilung", "Plenarprotokoll", "21/96", (11796, 11796)),  # not a Beratung
    ]


@pytest.fixture
def preliminary_store(data_dir):
    r = data_dir / "raw"
    _write(r / PROTOCOL, protocol_96(final=False), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-09-26T03:00:00+00:00")  # fmt: skip
    doc = {"id": "6096", "dokumentnummer": "21/96", "herausgeber": "BT", "text": dip_text()}
    _write(r / DIP_TEXT, json.dumps([doc]), "https://search.dip.bundestag.de/api/v1/plenarprotokoll-text?…",
           "2026-09-27T03:00:00+00:00")  # fmt: skip
    _write(r / POSITIONS, json.dumps(_positions()), "https://search.dip.bundestag.de/api/v1/vorgangsposition?…",
           "2026-09-27T03:00:00+00:00")  # fmt: skip
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    return conn


def test_preliminary_sitting_is_flagged_with_its_pages(preliminary_store):
    st = preliminary_store.execute("SELECT * FROM sitting WHERE id = '21/96'").fetchone()
    assert (st["preliminary"], st["final_announced"], st["final_fetched_at"]) == (1, "2026-10-02", None)
    assert (st["first_page"], st["last_page"]) == (11785, 11790)
    other = preliminary_store.execute("SELECT * FROM sitting WHERE id = '21/94'").fetchone()
    assert (other["preliminary"], other["final_fetched_at"]) == (0, other["retrieved_at"])


def test_pages_after_the_xml_come_from_dip(preliminary_store):
    rows = preliminary_store.execute("SELECT * FROM protocol_gap_page ORDER BY page").fetchall()
    assert [r["page"] for r in rows] == list(range(11791, 11799))
    assert rows[0]["id"] == "21/96/11791"
    assert rows[0]["text"].startswith("(A) Text der Seite 11791")
    assert rows[0]["source_document_id"] == "DIP Plenarprotokoll-Text 6096"
    assert rows[0]["retrieved_at"] == "2026-09-27T03:00:00+00:00"


def test_gap_query_sorts_the_unmatched_beratungen_by_cause(preliminary_store):
    (row,) = [r for r in queries.protocol_gaps(preliminary_store) if r["sitting_id"] == "21/96"]
    assert row["preliminary"] and row["final_announced"] == "2026-10-02"
    assert (row["last_page"], row["dip_last_page"], row["missing_pages"]) == (11790, 11798, "11791-11798")
    assert (row["fallback_pages"], row["fallback_page_count"]) == ("11791-11798", 8)
    causes = {b["vorgang_id"]: b["cause"] for b in row["beratungen"]}
    assert causes == {"v-late": "missing_pages", "v-other": "in_xml"}  # v-matched: TOP 8 names 21/5650
    assert row["source_document_id"] == "BT-PlPr. 21/96"


def test_no_fallback_without_page_markers(preliminary_store, data_dir):
    unmarked = protocol_96(final=False).replace('typ="druckseitennummer"', 'typ="other"')
    _write(data_dir / "raw" / PROTOCOL, unmarked, "u", "2026-09-26T03:00:00+00:00")
    ingest.ingest_all(preliminary_store)
    assert preliminary_store.execute("SELECT last_page FROM sitting WHERE id = '21/96'").fetchone()[0] is None
    assert preliminary_store.execute("SELECT COUNT(*) FROM protocol_gap_page").fetchone()[0] == 0
    (row,) = [r for r in queries.protocol_gaps(preliminary_store) if r["sitting_id"] == "21/96"]
    assert {b["cause"] for b in row["beratungen"]} == {"unknown"}


def test_final_protocol_replaces_the_flag_and_the_fallback(preliminary_store, data_dir):
    _write(data_dir / "raw" / PROTOCOL, protocol_96(final=True), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-10-03T03:00:00+00:00")  # fmt: skip
    ingest.ingest_all(preliminary_store)
    st = preliminary_store.execute("SELECT * FROM sitting WHERE id = '21/96'").fetchone()
    assert (st["preliminary"], st["final_fetched_at"], st["last_page"]) == (0, "2026-10-03T03:00:00+00:00", 11798)
    assert preliminary_store.execute("SELECT COUNT(*) FROM protocol_gap_page").fetchone()[0] == 0
    (row,) = [r for r in queries.protocol_gaps(preliminary_store) if r["sitting_id"] == "21/96"]
    assert {b["vorgang_id"] for b in row["beratungen"]} == {"v-other"}  # the late debate has its agenda item now


def test_old_store_gets_the_new_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE sitting (id TEXT PRIMARY KEY, wahlperiode INTEGER, number INTEGER, date TEXT)")
    old.commit()
    old.close()
    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sitting)")}
    assert {"preliminary", "final_announced", "final_fetched_at", "first_page", "last_page"} <= cols


# --- fetch ---------------------------------------------------------------------------------


def test_refetch_downloads_only_preliminary_protocols(data_dir, monkeypatch):
    _write(data_dir / "raw" / PROTOCOL, protocol_96(final=False), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-09-26T03:00:00+00:00")  # fmt: skip
    asked = []

    def fake_download(http, url, dest, *, force=False):
        asked.append((url, force))
        dest.write_text(protocol_96(final=True), encoding="utf-8")
        raw.write_meta(dest, url)
        return dest

    monkeypatch.setattr(raw, "download", fake_download)
    assert fetch_bundestag.preliminary_protocols(21) == [data_dir / "raw" / PROTOCOL]
    result = fetch_bundestag.refetch_preliminary(None, 21)
    assert asked == [("https://dserver.bundestag.de/btp/21/21096.xml", True)]
    assert result == [(data_dir / "raw" / PROTOCOL, False)]
    assert fetch_bundestag.preliminary_protocols(21) == []
    _write(data_dir / "raw" / PROTOCOL, protocol_96(final=False), "u", "2026-09-26T03:00:00+00:00")
    assert fetch_bundestag.refetch_preliminary(None, 21, skip=[data_dir / "raw" / PROTOCOL]) == []  # just fetched
    assert len(asked) == 1


def test_protocol_texts_are_asked_for_by_dokumentnummer(data_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_dip, "list_all", lambda http, endpoint, params: calls.append((endpoint, params)) or [])
    paths = fetch_dip.fetch_protocol_texts(None, 21, [96])
    assert calls == [("plenarprotokoll-text", {"f.zuordnung": "BT", "f.wahlperiode": 21, "f.dokumentnummer": "21/96"})]
    assert paths == [data_dir / "raw" / DIP_TEXT] and raw.read_json(paths[0]) == []


def test_update_refetches_and_asks_dip_for_preliminary_protocols(data_dir, monkeypatch):
    _write(data_dir / "raw" / PROTOCOL, protocol_96(final=False), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-09-26T03:00:00+00:00")  # fmt: skip
    calls = []
    monkeypatch.setenv("DIP_API_KEY", "test")
    monkeypatch.setattr(fetch_bundestag, "fetch_stammdaten", lambda http, force: None)
    monkeypatch.setattr(update, "fetch_new_protocols", lambda http, wp: [])
    monkeypatch.setattr(fetch_bundestag, "fetch_votes", lambda http, s, e: [])
    monkeypatch.setattr(update.fetch_wahl, "fetch_election", lambda http, name: [])
    monkeypatch.setattr(update.fetch_aw, "fetch_wahlperiode", lambda http, wp, force: None)
    monkeypatch.setattr(fetch_bundestag, "fetch_biografien", lambda http: [])
    monkeypatch.setattr(update.fetch_wikidata, "fetch_government", lambda http: [])
    monkeypatch.setattr(fetch_dip, "fetch_range", lambda *a, **k: None)
    monkeypatch.setattr(fetch_bundestag, "refetch_preliminary", lambda http, wp, skip=(): calls.append("refetch") or [])
    monkeypatch.setattr(fetch_dip, "fetch_protocol_texts", lambda http, wp, numbers: calls.append(numbers) or [])
    assert update.run(21, date(2026, 9, 28)) == 0
    assert calls == ["refetch", [96]]
