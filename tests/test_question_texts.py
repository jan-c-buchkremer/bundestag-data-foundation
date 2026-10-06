"""The texts of the Mündliche Fragen and their turns in the Fragestunde (question_text, question_table, question_turn;
bdf/parse_question_texts.py, bdf/ingest.py ``ingest_question_links``).

The fixtures are excerpts of real protocols (tests/fixtures/README.md): 21/52 with the start of its Fragestunde
(Fragen 1 and 2, with Nachfragen and Zusatzfragen) and five entries of its annex of written answers, and 21/53 with
the joint answer to Fragen 32 and 33, which missed the editorial deadline of 21/52."""

import json
import shutil

import pytest

from bdf import db, ingest, parse_protocol
from bdf.parse_question_texts import texts
from bdf.parse_question_turns import turns
from tests.conftest import FIXTURES

P52 = FIXTURES / "question_texts" / "21052.xml"
P53 = FIXTURES / "question_texts" / "21053.xml"


@pytest.fixture(scope="module")
def p52():
    return parse_protocol.parse(P52)


def _rows(protocol, path):
    rows, tables = texts(protocol, path)
    return {r["id"]: r for r in rows}, tables


def test_questions_read_out_in_the_fragestunde(p52):
    rows, _ = _rows(p52, P52)
    q1 = rows["21/3521/1/frage"]
    assert (q1["name"], q1["number"], q1["thread_id"]) == ("Stefan Schröder", "1", "21/52/1/f1")
    assert q1["text"].startswith("Welche Erkenntnisse hat die Bundesregierung") and q1["text"].endswith("?")
    assert rows["21/3521/2/frage"]["thread_id"] == "21/52/1/f6"
    # a spoken question has no answer row: the answer is the speech
    assert "21/3521/1/antwort" not in rows


def test_fragestunde_turns(p52):
    t = {r["speech_id"]: (r["role"], r["thread_id"]) for r in turns(p52)}
    assert [t[f"21/52/1/f{n}"] for n in (1, 2, 3)] == [
        ("antwort", "21/52/1/f1"), ("nachfrage", "21/52/1/f1"), ("antwort", "21/52/1/f1"),
    ]  # fmt: skip
    assert t["21/52/1/f7"] == ("nachfrage", "21/52/1/f6")  # Peterka, who asked Frage 2
    assert t["21/52/1/f11"] == ("zusatzfrage", "21/52/1/f6")  # Hahn
    assert all(r["vorgang_id"] is None for r in turns(p52))  # set after the DIP ingest


def test_written_answers(p52):
    rows, tables = _rows(p52, P52)
    assert "21/3521/30/frage" not in rows  # not answered under Nr. 9 Satz 2: no asker, no text printed
    q31, a31 = rows["21/3521/31/frage"], rows["21/3521/31/antwort"]
    assert (q31["name"], a31["name"]) == ("Mirze Edis", "Parl. Staatssekretärs Dennis Rohde")
    assert a31["answer_date"] == p52.date and a31["thread_id"] is None
    # Fragen 32 and 33: one header, two questions, the joint answer not there by the editorial deadline
    assert rows["21/3521/32/frage"]["text"].startswith("Wie verteilen sich")
    assert rows["21/3521/33/frage"]["text"].startswith("Wie viele Anträge")
    assert "21/3521/32/antwort" not in rows and "21/3521/33/antwort" not in rows
    # an answer with a table: the table's cells are kept apart, not in the text
    [table] = tables
    assert (table["question_text_id"], table["position"]) == ("21/3521/45/antwort", 1)
    cells = json.loads(table["cells"])
    assert cells["head"] == [["Grund des Aufenthalts", "Anzahl Personen"]]
    assert cells["body"][-1] == ["Gesamt", "43 626"]
    assert "Altfall" not in rows["21/3521/45/antwort"]["text"]  # a row label only the table has
    paragraphs = rows["21/3521/45/antwort"]["text"].split("\n\n")
    assert 0 < table["after_paragraph"] <= len(paragraphs)


def test_joint_answer_is_stored_with_each_question():
    p53 = parse_protocol.parse(P53)
    rows, tables = _rows(p53, P53)
    assert rows["21/3521/32/antwort"]["text"] == rows["21/3521/33/antwort"]["text"]
    assert [t["question_text_id"] for t in tables] == ["21/3521/32/antwort", "21/3521/33/antwort"]


def _dip(conn):
    """DIP rows for the fixture questions: the Fragen-Drucksache 21/3521 and five Vorgänge with their Frage activity
    in the protocol that prints them; Schmidt's two in 21/53 in reverse page order, told apart by their titles."""
    prov = ("https://example.org", "test", "2026-10-06T00:00:00+00:00")
    conn.execute(
        "INSERT INTO drucksache (id, number, wahlperiode, title, date, originators, source_url, source_document_id, "
        "retrieved_at) VALUES ('d3521', '21/3521', 21, 'Fragen', '2026-01-09', '[]', ?, ?, ?)",
        prov,
    )
    questions = [  # vorgang, title, asker, protocol, page
        ("v1", "Geförderte berufliche Weiterbildung", "Stefan Schröder, MdB, AfD", "21/52", "6001A"),
        ("v2", "Arbeitsmarktpolitische Vorhaben", "Tobias Matthias Peterka, MdB, AfD", "21/52", "6003B"),
        ("v31", "Energieinfrastruktur in Bundeshand", "Mirze Edis, MdB, DIE LINKE", "21/52", "6050A"),
        ("v33", "Entschädigungen aus dem Aufbauhilfefonds 2021", "Stefan Schmidt, MdB, GRÜNE", "21/53", "6100A"),
        ("v32", "Abgerufene Mittel für die Infrastruktur nach dem Hochwasser", "Stefan Schmidt, MdB, GRÜNE", "21/53",
         "6100B"),
    ]  # fmt: skip
    for k, (v, title, asker, protocol, page) in enumerate(questions):
        conn.execute(
            "INSERT INTO vorgang (id, wahlperiode, type, title, subjects, initiators, source_url, source_document_id, "
            "retrieved_at) VALUES (?, 21, 'Mündliche Frage', ?, '[]', '[]', ?, ?, ?)",
            (v, title, *prov),
        )
        conn.execute(
            "INSERT INTO vorgang_position (id, vorgang_id, date, position, document_kind, document_number, "
            "document_type, originators, source_url, source_document_id, retrieved_at) "
            "VALUES (?, ?, '2026-01-09', 'Mündliche Frage', 'Drucksache', '21/3521', 'Fragen', '[]', ?, ?, ?)",
            (f"p{v}", v, *prov),
        )
        conn.execute(
            "INSERT INTO question_activity (id, vorgang_id, question_type, activity_type, dip_person_id, name, "
            "document_kind, document_number, page, source_url, source_document_id, retrieved_at) "
            "VALUES (?, ?, 'Mündliche Frage', 'Frage', ?, ?, 'Plenarprotokoll', ?, ?, ?, ?, ?)",
            (f"a{k}", v, f"dp{k}", asker, protocol, page, *prov),
        )
    conn.execute(
        "INSERT INTO question_activity (id, vorgang_id, question_type, activity_type, dip_person_id, person_id, name, "
        "document_kind, document_number, page, source_url, source_document_id, retrieved_at) VALUES ('r31', 'v31', "
        "'Mündliche Frage', 'Antwort', 'dp9', '11004004', 'Dennis Rohde, Parl. Staatssekr.', 'Plenarprotokoll', "
        "'21/52', '6050A', ?, ?, ?)",
        prov,
    )
    conn.commit()


def test_store_and_dip_links(data_dir):
    target = data_dir / "raw" / "bundestag" / "protocols" / "21"
    for name in ("21052.xml", "21052.xml.meta.json", "21053.xml", "21053.xml.meta.json"):
        shutil.copy(FIXTURES / "question_texts" / name, target / name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    q = lambda sql: [tuple(r) for r in conn.execute(sql)]  # noqa: E731
    # 32 and 33 are printed twice; the later protocol, with the answer, holds them
    assert q("SELECT id, source_document_id FROM question_text WHERE number = '32' ORDER BY id") == [
        ("21/3521/32/antwort", "BT-PlPr. 21/53"), ("21/3521/32/frage", "BT-PlPr. 21/53"),
    ]  # fmt: skip
    assert q("SELECT count(*), count(vorgang_id) FROM question_text") == [(10, 0)]
    _dip(conn)
    ingest.ingest_question_links(conn)
    links = dict(q("SELECT id, vorgang_id FROM question_text WHERE part = 'frage'"))
    assert links == {
        "21/3521/1/frage": "v1", "21/3521/2/frage": "v2", "21/3521/31/frage": "v31",
        "21/3521/32/frage": "v32", "21/3521/33/frage": "v33", "21/3521/45/frage": None,
    }  # fmt: skip
    answer = "SELECT vorgang_id, answerer_person_id, drucksache_id FROM question_text WHERE id = '21/3521/31/antwort'"
    assert q(answer) == [("v31", "11004004", "d3521")]
    assert q("SELECT DISTINCT vorgang_id FROM question_turn WHERE thread_id = '21/52/1/f6'") == [("v2",)]
    ingest.ingest_protocols(conn)  # a re-ingest replaces the protocol's texts; the next link pass fills them again
    ingest.ingest_question_links(conn)
    assert dict(q("SELECT id, vorgang_id FROM question_text WHERE part = 'frage'")) == links
    assert q("PRAGMA foreign_key_check") == []
