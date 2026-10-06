"""The answers to Kleine Anfragen, read from their PDFs (bdf/parse_answers.py, ingest_answers, question_parse).

The fixtures are three answers, unchanged (tests/fixtures/README.md): 21/3326 (sub-questions answered one by one),
21/1343 (a joint answer, Frage 16 misprinted in the answer's type, "VS – Vertraulich" footnotes) and 21/669 (tables,
an annex of tables without ruling). Parsing caches its result next to the PDF, so the tests read copies."""

import json
import shutil

import pytest

from bdf import db, ingest, parse_answers
from tests.conftest import FIXTURES

ANSWERS = FIXTURES / "answers"


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("answers")
    out = {}
    for number in ("3326", "1343", "669"):
        name = f"21{int(number):05d}.pdf"
        shutil.copy(ANSWERS / name, tmp / name)
        texts, tables = parse_answers.rows(parse_answers.parse(tmp / name), f"21/{number}")
        out[number] = ({t["id"]: t for t in texts}, tables, tmp / name)
    return out


def test_head_and_note(answers):
    texts, _, _ = answers["1343"]
    answer = texts["21/1026/1/antwort"]
    assert (answer["drucksache_number"], answer["name"], answer["answer_date"]) == (
        "21/1026", "Bundesministeriums der Finanzen", "2025-08-22",
    )  # fmt: skip
    assert texts["21/1026/1/frage"]["answer_date"] is None and texts["21/1026/1/frage"]["name"] is None
    assert texts["21/1026/vorbemerkung_fragesteller"]["number"] is None
    assert texts["21/1026/vorbemerkung_bundesregierung"]["text"].startswith("Unternehmen unterliegen")


def test_questions_and_joint_answer(answers):
    texts, _, _ = answers["1343"]
    questions = [t for t in texts.values() if t["part"] == "frage"]
    assert sorted(int(t["number"]) for t in questions) == list(range(1, len(questions) + 1))
    assert texts["21/1026/4/antwort"]["text"] == texts["21/1026/5/antwort"]["text"]
    assert texts["21/1026/4/antwort"]["text"].startswith("Die Fragen 4 und 5 werden im Zusammenhang")
    # Frage 16 is set in the answer's type; it is still a question, and its answer follows
    assert texts["21/1026/16/frage"]["text"].startswith("Wie bewertet die Bundesregierung die Risiken")
    assert texts["21/1026/16/antwort"]["text"].startswith("Wie in der Vorbemerkung dargelegt")
    # footnotes are left out, and lines join into words and paragraphs
    assert not any("Geheimschutzordnung" in t["text"] for t in texts.values())
    assert "Kinder- und" not in texts["21/1026/1/frage"]["text"]
    assert all("  " not in t["text"] for t in texts.values())


def test_sub_questions_answered_one_by_one(answers):
    texts, _, _ = answers["3326"]
    assert [k.split("/", 2)[2] for k in texts][:6] == [
        "vorbemerkung_fragesteller", "1/frage", "1/antwort", "1a/frage", "1a/antwort", "1b/frage",
    ]  # fmt: skip
    assert texts["21/2852/1a/frage"]["text"] == "den aktuellen Planungsstand (bitte Leistungsphase angeben),"
    assert texts["21/2852/1f/antwort"]["text"] == "Es gilt auch hier die Antwort zu Frage 1e."
    # Frage 2 has its sub-items in the question and one answer after them
    assert "a) den aktuellen Planungsstand" in texts["21/2852/2/frage"]["text"]
    assert "21/2852/2a/frage" not in texts


def test_tables_and_annex(answers):
    texts, tables, _ = answers["669"]
    by_id = {t["id"]: json.loads(t["cells"]) for t in tables}
    assert by_id["21/439/8/antwort/1"]["head"] == [["Monat", "Ist"]]
    assert by_id["21/439/8/antwort/1"]["body"][0] == ["Januar", "8.196,8 T EUR"]
    assert not any("8.196,8" in t["text"] for t in texts.values())  # cut out of the text
    annex = texts["21/439/1/anlage"]
    assert annex["text"] == "Anlage zur KA 21/439 der Abgeordneten Clara Bünger u. a. und der Fraktion Die Linke"
    annex_tables = [c for k, c in by_id.items() if k.startswith("21/439/1/anlage/")]
    unread = [c for c in annex_tables if c.get("extracted") is False]
    assert (len(annex_tables), len(unread)) == (17, 11) and unread[0] == {"extracted": False, "page": 11}


def test_parse_is_cached(answers):
    _, _, path = answers["669"]
    cache = path.with_name(path.name + ".answer.json")
    assert json.loads(cache.read_text())["key"].startswith(f"{parse_answers.VERSION}:")
    assert parse_answers.parse(path) == parse_answers.parse(path)


def test_store(data_dir):
    target = data_dir / "raw" / "bundestag" / "drucksachen" / "21"
    target.mkdir(parents=True)
    for name in ("2101343.pdf", "2101343.pdf.meta.json"):
        shutil.copy(ANSWERS / name, target / name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    q = lambda sql: [tuple(r) for r in conn.execute(sql)]  # noqa: E731
    prov = "SELECT DISTINCT source_document_id, source_url FROM question_text WHERE drucksache_number = '21/1026'"
    assert q(prov) == [("BT-Drs. 21/1343", "https://dserver.bundestag.de/btd/21/013/2101343.pdf")]
    # the DIP data of the fixtures has no Vorgang for it: no link, no question_parse row
    assert q("SELECT count(*), count(vorgang_id) FROM question_text WHERE part = 'frage'") == [(35, 0)]
    prov = ("https://example.org", "test", "2026-10-06T00:00:00+00:00")
    conn.execute(
        "INSERT INTO vorgang (id, wahlperiode, type, title, subjects, initiators, source_url, source_document_id, "
        "retrieved_at) VALUES ('k1', 21, 'Kleine Anfrage', 'Payone', '[]', '[]', ?, ?, ?)",
        prov,
    )
    for did, number, kind in (("q", "21/1026", "Kleine Anfrage"), ("a", "21/1343", "Antwort")):
        conn.execute(
            "INSERT INTO drucksache (id, number, wahlperiode, type, title, date, originators, source_url, "
            "source_document_id, retrieved_at) VALUES (?, ?, 21, ?, 't', '2025-08-22', '[]', ?, ?, ?)",
            (did, number, kind, *prov),
        )
        conn.execute("INSERT INTO vorgang_drucksache VALUES ('k1', ?)", (did,))
    conn.execute(
        "INSERT INTO vorgang (id, wahlperiode, type, title, subjects, initiators, source_url, source_document_id, "
        "retrieved_at) VALUES ('k2', 21, 'Kleine Anfrage', 'not answered yet', '[]', '[]', ?, ?, ?)",
        prov,
    )
    conn.commit()
    ingest.ingest_question_links(conn)
    linked = "SELECT count(*) = count(vorgang_id), count(DISTINCT drucksache_id) FROM question_text"
    assert q(linked) == [(1, 1)]
    assert q("SELECT vorgang_id, status, questions, answered, source_document_id FROM question_parse ORDER BY 1") == [
        ("k1", "complete", 35, 35, "BT-Drs. 21/1343"), ("k2", "unanswered", 0, 0, None),
    ]  # fmt: skip


def test_answer_path_follows_dserver(data_dir):
    from bdf.fetch_bundestag import answer_path

    assert answer_path("21/1095").relative_to(data_dir).as_posix() == "raw/bundestag/drucksachen/21/2101095.pdf"
