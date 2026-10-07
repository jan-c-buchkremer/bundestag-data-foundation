"""hib: article parser, committee names, list fragment, ingest. The fixture articles keep bundestag.de's markup with
made-up body text (hib texts are protected, docs/licences.md)."""

from datetime import date

import httpx

from bdf import fetch_hib, parse_hib

from .conftest import FIXTURES

HIB = FIXTURES / "bundestag" / "hib"


def _item(item_id: str) -> parse_hib.Item:
    item = parse_hib.parse_article((HIB / f"{item_id}.html").read_text(encoding="utf-8"), item_id)
    assert item is not None
    return item


def test_article_header_body_and_links():
    item = _item("1223018")
    assert (item.number, item.date, item.ressort, item.kind) == ("784/2026", "2026-10-07", "Inneres", "Antwort")
    assert item.title == "Zukunft der unabhängigen Asylverfahrensberatung"
    assert item.author_code == "STO"
    assert item.text.startswith("Testtext zur Antwort der Bundesregierung (21/8309) auf")  # mark and labels gone
    assert "öffnet ein neues Fenster" not in item.text
    assert item.drucksachen == ["21/8309", "21/8029"]  # order of first mention, each once


def test_not_a_hib_article():
    assert parse_hib.parse_article("<html><body><h1>Seite nicht gefunden</h1></body></html>", "1") is None


def test_committee_compound_full_and_genitive():
    full = ["Ausschuss für Digitales und Staatsmodernisierung", "Ausschuss für Arbeit und Soziales"]
    assert parse_hib.committee(_item("1217090").text, full) == "Forschungsausschuss"  # the first one named
    assert parse_hib.committee(_item("1222652").text, full) == "Innenausschuss"  # genitive "des Innenausschusses"
    assert parse_hib.committee(_item("1217358").text, full) == "Ausschuss für Digitales und Staatsmodernisierung"
    assert parse_hib.committee("Der Ausschuss für Arbeit und Soziales, der am Mittwoch tagte", full) == (
        "Ausschuss für Arbeit und Soziales"
    )
    assert parse_hib.committee("Im Ausschuss wurde beraten.", full) is None


def test_list_page_days_and_ids():
    fragment = (
        '<h3> 7. Oktober 2026 </h3><a href="https://www.bundestag.de/presse/hib/kurzmeldungen-1223012">A</a>'
        '<a href="https://www.bundestag.de/presse/hib/kurzmeldungen-1223012">A</a>'
        '<h3> 30. September 2026 </h3><a href="/presse/hib/kurzmeldungen-1222000">B</a>'
    )
    assert parse_hib.list_page(fragment) == [("1223012", "2026-10-07"), ("1222000", "2026-09-30")]


def test_listed_stops_before_the_start(monkeypatch):
    pages = {
        0: '<h3>2. März 2026</h3><a href="/presse/hib/kurzmeldungen-3">x</a>'
        '<a href="/presse/hib/kurzmeldungen-2">x</a>',
        2: '<h3>1. März 2026</h3><a href="/presse/hib/kurzmeldungen-1">x</a>'
        '<h3>28. Februar 2026</h3><a href="/presse/hib/kurzmeldungen-0">x</a>',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(int(request.url.params["offset"]), ""))

    monkeypatch.setattr(fetch_hib, "PAUSE", 0)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert fetch_hib.listed(http, date(2026, 3, 1), stop_at_known=False) == ["3", "2", "1"]


def test_ingest(store):
    rows = {r["id"]: dict(r) for r in store.execute("SELECT * FROM hib_item")}
    assert set(rows) == {"1223018", "1217090", "1217358", "1222652"}
    assert rows["1223018"]["wahlperiode"] == 21
    assert rows["1223018"]["committee"] is None  # an Antwort names no committee
    assert rows["1217090"]["committee"] == "Forschungsausschuss"
    assert rows["1222652"]["committee"] == "Innenausschuss"
    assert rows["1223018"]["source_document_id"] == "hib 784/2026"
    assert rows["1223018"]["source_url"] == "https://www.bundestag.de/presse/hib/kurzmeldungen-1223018"
    links = store.execute("SELECT drucksache_number FROM hib_drucksache WHERE hib_id = '1223018' ORDER BY position")
    assert [r[0] for r in links] == ["21/8309", "21/8029"]
