"""Preliminary protocols: detection, the sitting flags, re-fetching, the PDF part and the gap query.

The preliminary and final variants of 21/96 are built here from the real excerpt in ``sub_items/`` by adding the
preliminary note and (final variant) one more agenda item; they test the flags, the gap query and re-fetching.
The PDF part is tested on real files in ``preliminary/``: the end of the preliminary 21/31 XML as served on
2026-10-01 and four pages of the final PDF (the XML's last page 3371, TOP 29 on 3392–3393, the end on 3404).
"""

import json
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

from bdf import db, fetch_bundestag, fetch_dip, ingest, protocol_status, queries, raw, update

FIXTURES = Path(__file__).parent / "fixtures"
EXCERPT = (FIXTURES / "sub_items" / "21096.xml").read_text(encoding="utf-8")
NOTE = "Der gesamte und damit endgültige Stenografische Bericht der 96. Sitzung wird am 2. Oktober 2026 veröffentlicht."


def protocol_96(*, final: bool) -> str:
    """21/96 excerpt; preliminary: plus the note; final: plus a Tagesordnungspunkt 12 on Drucksache 21/9999 at the
    end (the debate the preliminary version lacks)."""
    if final:
        tail = (
            '        <tagesordnungspunkt top-id="Tagesordnungspunkt 12">\n'
            '            <p klasse="T_NaS">Erste Beratung des Entwurfs eines Gesetzes über Lotsen</p>\n'
            '            <p klasse="T_Drs">Drucksache 21/9999</p>\n'
            "        </tagesordnungspunkt>\n"
        )
    else:
        tail = f'        <p klasse="J">{NOTE}</p>\n'
    return EXCERPT.replace("    </sitzungsverlauf>", tail + "    </sitzungsverlauf>", 1)


def _write(path: Path, text: str, url: str, retrieved_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    raw.meta_path(path).write_text(json.dumps({"url": url, "retrieved_at": retrieved_at}), encoding="utf-8")


def _status(xml: str) -> protocol_status.Status:
    return protocol_status.status(ET.fromstring(xml.split("\n", 1)[1]))


# --- detection ----------------------------------------------------------------------------


def test_preliminary_note_is_read():
    assert _status(protocol_96(final=False)) == protocol_status.Status(True, "2026-10-02", 11785)
    assert _status(protocol_96(final=True)) == protocol_status.Status(False, None, 11785)


@pytest.mark.parametrize(
    "path", ["bundestag/protocols/21/21094.xml", "decisions/21090.xml", "sub_items/21050.xml", "fragestunde/21013.xml"]
)
def test_real_final_protocols_are_not_preliminary(path):
    s = protocol_status.status(ET.parse(FIXTURES / path).getroot())
    assert not s.preliminary and s.final_announced is None and s.last_page is None
    assert s.first_page is not None


def test_preliminary_report_kind_alone_counts():
    xml = EXCERPT.replace("<sitzungsverlauf>", "<sitzungsverlauf><p>Vorläufiger Stenografischer Bericht</p>", 1)
    assert _status(xml) == protocol_status.Status(True, None, 11785)


@pytest.mark.parametrize(
    ("text", "iso"),
    [("Freitag, den 2. Oktober 2026", "2026-10-02"), ("02.10.2026", "2026-10-02"), ("12. März 2026", "2026-03-12"),
     ("demnächst", None)],
)  # fmt: skip
def test_german_date(text, iso):
    assert protocol_status.german_date(text) == iso


# --- store ---------------------------------------------------------------------------------

PROTOCOL = Path("bundestag/protocols/21/21096.xml")
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
        pos("p8", "v-go", "Wahlvorschlag", "Drucksache", "21/7777"),
        pos("p9", "v-go", "Geschäftsordnungsantrag zur Beratung", "Plenarprotokoll", "21/96", (11786, 11786)),
    ]


@pytest.fixture
def preliminary_store(data_dir):
    r = data_dir / "raw"
    _write(r / PROTOCOL, protocol_96(final=False), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-09-26T03:00:00+00:00")  # fmt: skip
    _write(r / POSITIONS, json.dumps(_positions()), "https://search.dip.bundestag.de/api/v1/vorgangsposition?…",
           "2026-09-27T03:00:00+00:00")  # fmt: skip
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    return conn


def test_preliminary_sitting_is_flagged(preliminary_store):
    st = preliminary_store.execute("SELECT * FROM sitting WHERE id = '21/96'").fetchone()
    assert (st["preliminary"], st["final_announced"], st["final_fetched_at"]) == (1, "2026-10-02", None)
    assert (st["first_page"], st["last_page"]) == (11785, None)  # no PDF: where the XML ends is not known
    other = preliminary_store.execute("SELECT * FROM sitting WHERE id = '21/94'").fetchone()
    assert (other["preliminary"], other["final_fetched_at"]) == (0, other["retrieved_at"])


def test_gap_query_lists_the_unmatched_beratungen(preliminary_store):
    (row,) = [r for r in queries.protocol_gaps(preliminary_store) if r["sitting_id"] == "21/96"]
    assert row["preliminary"] and row["final_announced"] == "2026-10-02"
    assert (row["last_page"], row["pdf_speeches"]) == (None, 0)
    causes = {b["vorgang_id"]: b["cause"] for b in row["beratungen"]}
    # v-matched: TOP 8 names 21/5650; the Mitteilung of v-other and v-go's motion on the agenda are no Beratung
    assert causes == {"v-late": "preliminary", "v-other": "preliminary"}
    assert row["source_document_id"] == "BT-PlPr. 21/96"


def test_drucksache_decided_under_an_item_closes_its_gap(preliminary_store):
    item = preliminary_store.execute("SELECT * FROM agenda_item WHERE sitting_id = '21/96' LIMIT 1").fetchone()
    preliminary_store.execute(
        "INSERT INTO agenda_item_vorlage (id, agenda_item_id, drucksache_number, via, source_url, source_document_id,"
        " retrieved_at) VALUES (?, ?, '21/8888', 'decision', 'x', 'x', 'x')",
        (f"{item['id']}/21/8888", item["id"]),
    )
    (row,) = [r for r in queries.protocol_gaps(preliminary_store) if r["sitting_id"] == "21/96"]
    assert {b["vorgang_id"] for b in row["beratungen"]} == {"v-late"}


def test_final_protocol_replaces_the_flag_and_the_fallback(preliminary_store, data_dir):
    _write(data_dir / "raw" / PROTOCOL, protocol_96(final=True), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-10-03T03:00:00+00:00")  # fmt: skip
    ingest.ingest_all(preliminary_store)
    st = preliminary_store.execute("SELECT * FROM sitting WHERE id = '21/96'").fetchone()
    assert (st["preliminary"], st["final_fetched_at"], st["last_page"]) == (0, "2026-10-03T03:00:00+00:00", None)
    (row,) = [r for r in queries.protocol_gaps(preliminary_store) if r["sitting_id"] == "21/96"]
    # the late debate has its agenda item now; the other one is in the protocol under another Drucksache
    assert {b["vorgang_id"]: b["cause"] for b in row["beratungen"]} == {"v-other": "in_protocol"}


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


def test_update_refetches_preliminary_protocols_and_their_pdfs(data_dir, monkeypatch):
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
    monkeypatch.setattr(update.fetch_hib, "fetch", lambda http, since: [])
    monkeypatch.setattr(fetch_bundestag, "refetch_preliminary", lambda http, wp, skip=(): calls.append("refetch") or [])
    monkeypatch.setattr(fetch_bundestag, "fetch_preliminary_pdfs", lambda http, wp: calls.append("pdfs") or [])
    assert update.run(21, date(2026, 9, 28)) == 0
    assert calls == ["refetch", "pdfs"]


def test_preliminary_pdfs_are_written_only_when_changed(data_dir, monkeypatch):
    _write(data_dir / "raw" / PROTOCOL, protocol_96(final=False), "https://dserver.bundestag.de/btp/21/21096.xml",
           "2026-09-26T03:00:00+00:00")  # fmt: skip
    asked = []

    class Response:
        content = b"%PDF-1.7 final"

    monkeypatch.setattr(raw, "get", lambda http, url: asked.append(url) or Response())
    pdf = (data_dir / "raw" / PROTOCOL).with_suffix(".pdf")
    assert fetch_bundestag.fetch_preliminary_pdfs(None, 21) == [(pdf, True)]
    assert asked == ["https://dserver.bundestag.de/btp/21/21096.pdf"]
    assert pdf.read_bytes() == b"%PDF-1.7 final" and raw.read_meta(pdf).url == asked[0]
    mtime = pdf.stat().st_mtime_ns
    assert fetch_bundestag.fetch_preliminary_pdfs(None, 21) == [(pdf, False)]  # unchanged: not rewritten
    assert pdf.stat().st_mtime_ns == mtime


# --- the PDF part (real files: the end of the preliminary 21/31 XML, four pages of the final PDF) ----------------


def _join(lines: list[str]) -> str:
    from bdf.protocol_pdf import _join

    return _join(lines)


def test_pdf_lines_are_joined_without_the_hyphenation():
    assert _join(["Bun-", "despolitik"]) == "Bundespolitik"
    assert _join(["der CDU/CSU-", "Fraktion"]) == "der CDU/CSU-Fraktion"
    assert _join(["BÜNDNIS-", "SES 90/DIE GRÜNEN"]) == "BÜNDNISSES 90/DIE GRÜNEN"
    assert _join(["Sicherheit –", "und"]) == "Sicherheit – und"


def _lines(*specs: tuple) -> list:
    """Lines from (text, indent) or (text, indent, size, bold); body text at 10 pt regular."""
    from bdf.protocol_pdf import Line

    out = []
    for i, (text, indent, *rest) in enumerate(specs):
        size, bold = rest if rest else (10.0, False)
        out.append(Line(page=100, text=text, indent=indent, top=i * 11, size=size, bold=bold, bold_start=bold))
    return out


def _paragraphs(*specs: tuple) -> list[tuple[str, str]]:
    from bdf.protocol_pdf import paragraphs

    return [(p.kind, p.text) for p in paragraphs(_lines(*specs))]


def test_pdf_reading_stops_at_the_end_of_the_sitting_and_before_the_anlagen():
    paras = _paragraphs(
        ("Die Sitzung ist geschlossen.", 10),
        ("(Schluss: 00:29 Uhr)", 0),  # printed without indent (21/14)
        ("Anlage 1", 0, 10.0, True),
        ("Hans Koller (CDU/CSU):", 9, 9.5, True),
        ("Zu Protokoll gegebene Rede", 10),
    )
    assert paras == [("text", "Die Sitzung ist geschlossen."), ("comment", "(Schluss: 00:29 Uhr)")]
    # without the Schluss line (it was on a page the reading missed), the Anlage heading ends it
    assert _paragraphs(("Ende.", 10), ("Anlage", 0, 10.0, True), ("Text", 10)) == [("text", "Ende.")]


def test_pdf_block_quote_is_one_paragraph():
    paras = _paragraphs(
        ("Ich zitiere den Koalitionsvertrag:", 10),
        ("„Wir werden die Schere zwischen der Entlastungs-", 10),
        ("wirkung der Kinderfreibeträge und dem Kindergeld", 10),
        ("schrittweise verringern.“", 10),
        ("Danach geht es weiter.", 10),
    )
    assert paras == [
        ("text", "Ich zitiere den Koalitionsvertrag:"),
        ("text", "„Wir werden die Schere zwischen der Entlastungswirkung der Kinderfreibeträge und dem Kindergeld "
                 "schrittweise verringern.“"),
        ("text", "Danach geht es weiter."),
    ]  # fmt: skip


def test_pdf_comment_ends_at_the_next_speaker_even_unclosed():
    paras = _paragraphs(
        ("(Beifall bei der AfD – Zuruf von der SPD: Das", 30),
        ("ist doch", 30),
        ("Dr. Stefan Nacke (CDU/CSU):", 9, 9.5, True),
        ("Frau Präsidentin!", 10),
    )
    assert [k for k, _ in paras] == ["comment", "speaker", "text"]


def test_pdf_right_page_with_a_page_range_is_read():
    from bdf.protocol_pdf import _PAGE_RIGHT_RE

    head = "Deutscher Bundestag – 21. Wahlperiode – 40. Sitzung. Berlin, Donnerstag, den 13. November 2025 4 7 0 3 - 4 7 1 8"  # noqa: E501
    m = _PAGE_RIGHT_RE.match(head)
    assert m and m.group(1).replace(" ", "") == "4703"


@pytest.fixture
def pdf_store(data_dir):
    protocols = data_dir / "raw" / "bundestag" / "protocols" / "21"
    for f in (FIXTURES / "preliminary").iterdir():
        shutil.copy(f, protocols / f.name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    return conn


PDF_URL = "https://dserver.bundestag.de/btp/21/21031.pdf"


def test_pdf_part_supplies_the_agenda_items_the_xml_lacks(pdf_store):
    st = pdf_store.execute("SELECT * FROM sitting WHERE id = '21/31'").fetchone()
    assert (st["preliminary"], st["final_announced"], st["last_page"]) == (1, "2025-10-14", 3371)
    items = pdf_store.execute("SELECT * FROM agenda_item WHERE sitting_id = '21/31' ORDER BY position").fetchall()
    assert [i["top_id"] for i in items] == ["Tagesordnungspunkt 22", "Tagesordnungspunkt 23", "Tagesordnungspunkt 29"]
    assert items[0]["source_url"] == "https://dserver.bundestag.de/btp/21/21031.xml"
    top23, top29 = items[1], items[2]
    assert json.loads(top23["drucksache_numbers"]) == ["21/1595"]
    assert json.loads(top29["drucksache_numbers"]) == ["21/1858"]
    assert "SGB VI-Anpassungsgesetz" in top29["title"]
    assert (top29["source_url"], top29["source_document_id"]) == (PDF_URL, "BT-PlPr. 21/31 (PDF)")
    texts = [r[0] for r in pdf_store.execute(
        "SELECT p.text FROM agenda_item_paragraph p JOIN agenda_item a ON a.id = p.agenda_item_id "
        "WHERE a.sitting_id = '21/31'"
    )]  # fmt: skip
    assert "Ich rufe den Tagesordnungspunkt 29 auf:" in texts
    assert not any("endgültige Stenografische Bericht" in t for t in texts)  # the preliminary note is dropped


def test_pdf_part_speeches_have_their_speakers_and_the_pdf_as_source(pdf_store):
    rows = pdf_store.execute(
        "SELECT s.*, a.top_id FROM speech s JOIN agenda_item a ON a.id = s.agenda_item_id "
        "WHERE s.sitting_id = '21/31' AND s.source_url = ? ORDER BY s.position",
        (PDF_URL,),
    ).fetchall()
    griese = next(r for r in rows if r["speaker_name"].startswith("Kerstin Griese"))
    assert griese["person_id"] == "11003440"  # matched by name to her speaker line in 21/49
    assert griese["speaker_role"] == "Parl. Staatssekretärin bei der Bundesministerin für Arbeit und Soziales"
    assert griese["top_id"] == "Tagesordnungspunkt 29"
    assert griese["text"].startswith("Sehr geehrter Herr Präsident! Liebe Kolleginnen und Kollegen! Ich bringe das")
    assert "Fallmanagement der Rentenversicherungsträger" in griese["text"]  # "Fallmanage-" / "ment"
    stephan = next(r for r in rows if r["speaker_name"] == "Thomas Stephan (AfD)")
    assert (stephan["person_id"], stephan["fraction"]) == ("pdf-thomas-stephan", "AfD")  # in no fixture XML
    assert pdf_store.execute("SELECT is_mdb FROM person WHERE id = 'pdf-thomas-stephan'").fetchone()[0] == 0
    # the presidency is never a speaker; comments stay comments
    assert not any(r["speaker_name"].startswith(("Präsident", "Vizepräsident")) for r in rows)
    kinds = {r[0] for r in pdf_store.execute(
        "SELECT kind FROM speech_paragraph WHERE speech_id = ?", (griese["id"],))}  # fmt: skip
    assert {"text", "comment", "chair"} <= kinds
    xml_speeches = pdf_store.execute(
        "SELECT COUNT(*) FROM speech WHERE sitting_id = '21/31' AND source_url LIKE '%.xml'"
    ).fetchone()[0]
    assert xml_speeches > 0  # TOP 22 keeps the XML as its source


def test_pdf_part_stops_at_the_end_of_the_sitting(pdf_store):
    texts = [r[0] for r in pdf_store.execute(
        "SELECT sp.text FROM speech_paragraph sp JOIN speech s ON s.id = sp.speech_id WHERE s.sitting_id = '21/31' "
        "UNION ALL SELECT p.text FROM agenda_item_paragraph p JOIN agenda_item a ON a.id = p.agenda_item_id "
        "WHERE a.sitting_id = '21/31'"
    )]  # fmt: skip
    assert not any(t.startswith("(Schluss:") for t in texts)
    assert any("Wir sind damit am Schluss unserer heutigen Tagesordnung" in t for t in texts)


def test_final_xml_replaces_the_pdf_part(pdf_store, data_dir):
    path = data_dir / "raw" / "bundestag" / "protocols" / "21" / "21031.xml"
    final = "\n".join(ln for ln in path.read_text(encoding="utf-8").split("\n") if 'klasse="Ende"' not in ln)
    _write(path, final, "https://dserver.bundestag.de/btp/21/21031.xml", "2025-10-15T03:00:00+00:00")
    ingest.ingest_all(pdf_store)
    st = pdf_store.execute("SELECT * FROM sitting WHERE id = '21/31'").fetchone()
    assert (st["preliminary"], st["last_page"], st["final_fetched_at"]) == (0, None, "2025-10-15T03:00:00+00:00")
    for table in ("agenda_item", "speech"):
        n = pdf_store.execute(f"SELECT COUNT(*) FROM {table} WHERE sitting_id = '21/31' AND source_url = ?",
                              (PDF_URL,)).fetchone()[0]  # fmt: skip
        assert n == 0, table


def test_entschliessungsantrag_decided_under_an_item_is_a_vorlage_of_it(data_dir):
    protocols = data_dir / "raw" / "bundestag" / "protocols" / "21"
    for name in ("21090.xml", "21090.xml.meta.json"):
        shutil.copy(FIXTURES / "decisions" / name, protocols / name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    (row,) = conn.execute("SELECT * FROM agenda_item_vorlage WHERE drucksache_number = '21/7071'").fetchall()
    decision = conn.execute("SELECT * FROM decision WHERE drucksache_number LIKE '%21/7071%'").fetchone()
    assert (row["via"], row["agenda_item_id"]) == ("decision", decision["agenda_item_id"])
    item = conn.execute("SELECT drucksache_numbers FROM agenda_item WHERE id = ?", (row["agenda_item_id"],)).fetchone()
    assert "21/7071" not in json.loads(item[0])  # the item's title does not name it
    assert conn.execute("SELECT COUNT(*) FROM agenda_item_vorlage WHERE via = 'title'").fetchone()[0] > 0
