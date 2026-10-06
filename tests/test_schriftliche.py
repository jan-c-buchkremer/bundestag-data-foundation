"""The Schriftliche Fragen of one week, read from their Sammeldrucksache (bdf/parse_schriftliche.py, ingest_answers,
the links and question_parse in ingest_question_links).

The fixture is the Sammeldrucksache 21/2290 (questions answered in the week of 13 October 2025), unchanged
(tests/fixtures/README.md): 81 questions in 13 ministries, joint answers, tables. Parsing caches its result next to
the PDF, so the tests read a copy."""

import json
import shutil

import pytest

from bdf import db, ingest, parse_schriftliche
from tests.conftest import FIXTURES

PDF = FIXTURES / "schriftliche" / "2102290.pdf"


@pytest.fixture(scope="module")
def week(tmp_path_factory):
    path = tmp_path_factory.mktemp("schriftliche") / PDF.name
    shutil.copy(PDF, path)
    collection = parse_schriftliche.parse(path)
    texts, tables = parse_schriftliche.rows(collection, "21/2290")
    return collection, {t["id"]: t for t in texts}, tables


def test_entries(week):
    collection, _, _ = week
    assert [e.number for e in collection.entries] == [str(n) for n in range(1, 82)]
    first = collection.entries[0]
    assert (first.asker, first.fraction) == ("Julian Joswig", "BÜNDNIS 90/DIE GRÜNEN")
    assert first.ministry.startswith("Geschäftsbereich des Bundeskanzlers und des Bundeskanzleramt")
    assert (first.answer_name, first.answer_date) == ("des Staatssekretärs Stefan Kornelius", "2025-10-14")
    assert first.question[0].startswith("Wurden Interviews, Presse-/Hintergrundgespräche")
    assert all(e.question and e.answer and e.answer_name for e in collection.entries)


def test_rows(week):
    _, texts, tables = week
    assert len(texts) == 162  # every question with its answer
    q, a = texts["21/2290/36/frage"], texts["21/2290/36/antwort"]
    assert (q["name"], q["answer_date"], q["drucksache_number"]) == ("Dr. Rainer Kraft", None, "21/2290")
    assert (a["name"], a["answer_date"]) == ("des Staatssekretärs Frank Wetzel", "2025-10-20")
    assert a["text"].startswith("Die Netzbetreiber sind nach § 11 des Energiewirtschaftsgesetzes")
    # 37, by the same asker, is answered on its own (DIP keeps 36 and 37 as one Vorgang)
    assert texts["21/2290/37/antwort"]["answer_date"] == "2025-10-17"
    assert [t["id"] for t in tables][:2] == ["21/2290/5/antwort/1", "21/2290/43/antwort/1"]
    assert "head" in json.loads(tables[0]["cells"]) or "extracted" in json.loads(tables[0]["cells"])


def test_store_and_links(data_dir):
    target = data_dir / "raw" / "bundestag" / "drucksachen" / "21"
    target.mkdir(parents=True)
    for name in (PDF.name, PDF.name + ".meta.json"):
        shutil.copy(PDF.parent / name, target / name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    prov = ("https://example.org", "test", "2026-10-06T00:00:00+00:00")
    # DIP: the Sammeldrucksache, and the Vorgänge of questions 1 and 36/37 (one Vorgang for both, as DIP keeps it)
    ingest.ingest_all(conn)
    conn.execute(
        "INSERT INTO drucksache (id, number, wahlperiode, type, title, date, originators, source_url, "
        "source_document_id, retrieved_at) VALUES ('s', '21/2290', 21, 'Schriftliche Fragen', 't', '2025-10-17', "
        "'[]', ?, ?, ?)",
        prov,
    )
    for k, (v, numbers, asker) in enumerate((("v1", "1", "Julian Joswig, MdB, GRÜNE"), ("v36", "36, 37", "Dr. Rainer "
                                             "Kraft, MdB, AfD"), ("v9", "9", "Someone Else, MdB, AfD"))):  # fmt: skip
        conn.execute(
            "INSERT INTO vorgang (id, wahlperiode, type, title, subjects, initiators, source_url, source_document_id, "
            "retrieved_at) VALUES (?, 21, 'Schriftliche Frage', 't', '[]', '[]', ?, ?, ?)",
            (v, *prov),
        )
        conn.execute(
            "INSERT INTO question_activity (id, vorgang_id, question_type, activity_type, dip_person_id, name, "
            "document_kind, document_number, question_numbers, source_url, source_document_id, retrieved_at) "
            "VALUES (?, ?, 'Schriftliche Frage', 'Frage', ?, ?, 'Drucksache', '21/2290', ?, ?, ?, ?)",
            (f"f{k}", v, f"d{k}", asker, numbers, *prov),
        )
    conn.execute(
        "INSERT INTO question_activity (id, vorgang_id, question_type, activity_type, dip_person_id, person_id, name, "
        "document_kind, document_number, question_numbers, source_url, source_document_id, retrieved_at) VALUES "
        "('r1', 'v1', 'Schriftliche Frage', 'Antwort', 'dr', '11004004', 'Stefan Kornelius, Staatssekr.', "
        "'Drucksache', '21/2290', '1', ?, ?, ?)",
        prov,
    )
    conn.commit()
    ingest.ingest_answers(conn)  # now that DIP calls 21/2290 a Sammeldrucksache
    ingest.ingest_question_links(conn)
    q = lambda sql: [tuple(r) for r in conn.execute(sql)]  # noqa: E731
    assert q("SELECT count(*) FROM question_text WHERE source_document_id = 'BT-Drs. 21/2290'") == [(162,)]
    links = "SELECT id, vorgang_id, answerer_person_id FROM question_text WHERE vorgang_id IS NOT NULL ORDER BY id"
    assert q(links) == [
        ("21/2290/1/antwort", "v1", "11004004"), ("21/2290/1/frage", "v1", None),
        ("21/2290/36/antwort", "v36", None), ("21/2290/36/frage", "v36", None),
        ("21/2290/37/antwort", "v36", None), ("21/2290/37/frage", "v36", None),
    ]  # fmt: skip
    # number 9 belongs to another asker in DIP: no link; its Vorgang is not read
    parse = "SELECT vorgang_id, status FROM question_parse WHERE vorgang_id IN ('v1', 'v36', 'v9') ORDER BY 1"
    assert q(parse) == [("v1", "complete"), ("v36", "complete"), ("v9", "failed")]
