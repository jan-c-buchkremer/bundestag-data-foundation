"""The turns of a Befragung der Bundesregierung (question_turn, bdf/parse_question_turns.py).

The fixtures are excerpts of real protocols (tests/fixtures/README.md): 21/6 TOP 1 up to the fifth Hauptfrage (two
opening statements, a Fraktionsrunde, then Hauptfragen with the follow-ups of other members), and 21/82 TOP 1 from
Büdenbender's Hauptfrage on, where Minister Rainer is printed as "Alois Rainer (CDU/CSU)" in some answers."""

import shutil

import pytest

from bdf import db, ingest, parse_protocol
from bdf.parse_question_turns import call, turns
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def p6():
    return parse_protocol.parse(FIXTURES / "question_turns" / "21006.xml")


@pytest.fixture(scope="module")
def p82():
    return parse_protocol.parse(FIXTURES / "question_turns" / "21082.xml")


def _by_id(protocol):
    return {r["speech_id"]: (r["role"], r["thread_id"]) for r in turns(protocol)}


def test_every_turn_of_the_befragung_has_a_row(p6):
    rows = turns(p6)
    assert [r["speech_id"] for r in rows] == [s.id for s in p6.speeches if s.agenda_item_id == "21/6/1"]
    assert all(r["vorgang_id"] is None for r in rows)


def test_opening_statements_questions_and_answers(p6):
    t = _by_id(p6)
    assert t["ID21600100"] == t["ID21600200"] == ("einleitung", None)  # Dobrindt, Hubertz
    # Fraktionsrunde: "Für die Fraktion der CDU/CSU hat nun … Oster das Wort", then "Sie haben die Möglichkeit für eine
    # Nachfrage"; the answers belong to the question
    assert [t[i] for i in ("ID21600700", "ID21600800", "ID21600900", "ID21601000")] == [
        ("frage", "ID21600700"), ("antwort", "ID21600700"), ("nachfrage", "ID21600700"), ("antwort", "ID21600700"),
    ]  # fmt: skip
    # Gennburg asks on inside the minister's rede (a part "-3")
    assert t["ID21602100-3"] == ("nachfrage", "ID21601900")


def test_follow_ups_of_other_members(p6):
    t = _by_id(p6)
    # "Ich lasse jetzt hierzu noch zwei Nachfragen zu. – Einmal von der AfD-Fraktion, … Bachmann"; then Stracke, called
    # by fraction only, asks once: the second of the two
    assert t["ID21603700"] == t["ID21603900"] == ("zusatzfrage", "ID21603300")
    # "Ich lasse zu dieser Hauptfrage drei Nachfragen zu": Brandner, Throm (by fraction only), Kopf ("Es gibt jetzt
    # noch eine Nachfrage: Für die Fraktion Bündnis 90/Die Grünen …")
    assert [t[i] for i in ("ID21606100", "ID21606300", "ID21606500")] == [("zusatzfrage", "ID21605700")] * 3
    # "Für die nächste Hauptfrage erteile ich das Wort dem Abgeordneten Martin Hess"
    assert t["ID21606700"] == ("frage", "ID21606700")


def test_mixed_call_is_left_to_the_structure(p6):
    # "damit heute noch weitere Fragen drankommen können, zu einer nächsten Hauptfrage": Eichwede asks again, so hers
    # is a question of its own
    t = _by_id(p6)
    assert (t["ID21605100"], t["ID21605300"]) == (("frage", "ID21605100"), ("nachfrage", "ID21605100"))


def test_minister_printed_without_office_answers(p82):
    t = _by_id(p82)
    rainer = [s for s in p82.speeches if s.speaker.last_name == "Rainer"]
    assert {s.speaker.role for s in rainer} == {"Bundesminister für Landwirtschaft, Ernährung und Heimat", None}
    assert {t[s.id][0] for s in rainer} == {"antwort"}
    # "Ich habe die Frageliste für diese Frage geschlossen, weil innerhalb der Fraktionsfragerunde jede Fraktion zu Wort
    # kommen muss. Deshalb hat jetzt für die AfD-Fraktion … Blos das Wort." and the last one "zu dieser Hauptfrage"
    assert [t[i] for i in ("ID218205300", "ID218205500", "ID218205700", "ID218205900")] == [
        ("zusatzfrage", "ID218204900")
    ] * 4


@pytest.mark.parametrize(
    ("chair", "said"),
    [
        (["Wir kommen zur nächsten Hauptfrage. Für die CDU/CSU-Fraktion hat jetzt Florian Müller das Wort."], "new"),
        (["Damit kommen wir zur nächsten Hauptfrage. Und das Wort dazu bekommt die Abgeordnete Chantal Kopf."], "new"),
        (["Damit wandert das Fragerecht an die SPD-Fraktion. Herr Abgeordneter Sebastian Roloff."], "new"),
        (["Eine weitere Nachfrage: für die AfD-Fraktion Frau Abgeordnete Nicole Hess."], "follow"),
        (["Die letzte Nachfrage zu dieser Hauptfrage stellt Tarek Al-Wazir."], "follow"),
        (["Frau Beck hat sich gemeldet. Bitte."], "follow"),
        (["Hierzu gibt es keine weiteren Nachfragen. – Dann hat jetzt für die SPD Hakan Demir das Wort."], None),
        (["Wir haben weitere Nachfragen. Für die Fraktion der AfD hat jetzt Herr Martin Sichert das Wort."], None),
        (["Herr Bauer, bitte."], None),
        ([], None),
    ],
)
def test_call(chair, said):
    assert call(chair) == said


def test_store_rows_and_reingest(data_dir):
    target = data_dir / "raw" / "bundestag" / "protocols" / "21"
    for name in ("21006.xml", "21006.xml.meta.json"):
        shutil.copy(FIXTURES / "question_turns" / name, target / name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    q = lambda sql: [tuple(r) for r in conn.execute(sql)]  # noqa: E731
    assert q("SELECT role, count(*) FROM question_turn GROUP BY role ORDER BY role") == [
        ("antwort", 35), ("einleitung", 2), ("frage", 11), ("nachfrage", 12), ("zusatzfrage", 12),
    ]  # fmt: skip
    assert q("SELECT count(*) FROM speech WHERE agenda_item_id = '21/6/1'") == [(72,)]
    assert q("SELECT kind FROM agenda_item WHERE id = '21/6/1'") == [("befragung",)]
    before = q("SELECT * FROM question_turn ORDER BY speech_id")
    ingest.ingest_protocols(conn)  # replaces the sitting's speeches, and their turns with them
    assert q("SELECT * FROM question_turn ORDER BY speech_id") == before
    assert q("PRAGMA foreign_key_check(question_turn)") == []
