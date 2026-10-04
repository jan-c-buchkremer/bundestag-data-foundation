"""Fraction vocabulary and speaker groups (bdf/names.py, ingest.ingest_groups)."""

import json

import pytest

from bdf import ingest, queries
from bdf.db import upsert
from bdf.names import originator_group, party_fraction, speaker_group


@pytest.mark.parametrize(
    ("party", "fraction"),
    [("CDU", "CDU/CSU"), ("CSU", "CDU/CSU"), ("GRÜNE", "BÜNDNIS 90/DIE GRÜNEN"), ("DIE LINKE.", "Die Linke"),
     ("Die Linke", "Die Linke"), ("AfD", "AfD"), ("SSW", None), ("FDP", None), (None, None)],
)  # fmt: skip
def test_party_fraction(party, fraction):
    assert party_fraction(party) == fraction


@pytest.mark.parametrize(
    ("title", "group"),
    [("Fraktion der SPD", "SPD"), ("Fraktion DIE LINKE", "Die Linke"), ("Fraktion BÜNDNIS 90/DIE GRÜNEN",
     "BÜNDNIS 90/DIE GRÜNEN"), ("Fraktion der CDU/CSU", "CDU/CSU"), ("Bundesregierung", "Bundesregierung"),
     ("Bundesministerium der Finanzen", "Bundesregierung"), ("Haushaltsausschuss", None), ("Bundesrat", None),
     ("Präsident des Deutschen Bundestages", None)],
)  # fmt: skip
def test_originator_group(title, group):
    assert originator_group(title) == group


@pytest.mark.parametrize(
    ("role", "fraction", "government", "group"),
    [(None, "SPD", False, "SPD"),
     ("Bundeskanzler", None, True, "Bundesregierung"),
     ("Beauftragte der Bundesregierung für Ostdeutschland", None, False, "Bundesregierung"),
     ("Staatsministerin (Hessen)", None, False, "Bundesrat"),
     ("Ministerpräsidentin (Mecklenburg-Vorpommern)", None, False, "Bundesrat"),
     ("Wehrbeauftragter des Deutschen Bundestages", None, False, "Sonstige"),
     (None, None, False, "Sonstige")],
)  # fmt: skip
def test_speaker_group(role, fraction, government, group):
    assert speaker_group(role, fraction, government) == group


def test_ingest_groups(store):
    # a minister's speech counts for the government; her fraction is kept beside it
    bas = store.execute("SELECT speaker_group, member_fraction FROM speech WHERE id = 'ID219400100'").fetchone()
    assert tuple(bas) == ("Bundesregierung", "SPD")
    assert store.execute("SELECT count(*) FROM speech WHERE speaker_group IS NULL").fetchone()[0] == 0
    assert store.execute("SELECT fraction FROM person WHERE id = '11004006'").fetchone()[0] == "SPD"
    groups = dict(store.execute("SELECT number, originator_groups FROM drucksache"))
    assert json.loads(groups["21/6977"]) == ["Die Linke"] and json.loads(groups["21/7107"]) == []
    parties = dict(store.execute("SELECT party, fraction FROM party_fraction"))
    assert parties["CSU"] == "CDU/CSU" and parties["GRÜNE"] == "BÜNDNIS 90/DIE GRÜNEN"


def _rede_parts(conn, item_title, parts):
    """A sitting 21/998 with one agenda item and the given speech parts [(id, person, words, chair text)]."""
    prov = {"source_url": "u", "source_document_id": "d", "retrieved_at": "2026-01-01"}
    upsert(conn, "sitting", [{"id": "21/998", "wahlperiode": 21, "number": 998, "date": "2026-01-01",
                              "xml_url": "x", "pdf_url": "p", **prov}])  # fmt: skip
    upsert(conn, "agenda_item", [{"id": "21/998/1", "sitting_id": "21/998", "position": 1, "top_id": "TOP 1",
                                  "title": item_title, "drucksache_numbers": "[]", **prov}])  # fmt: skip
    for n, (sid, person, words, chair) in enumerate(parts, start=1):
        upsert(conn, "speech", [{"id": sid, "sitting_id": "21/998", "agenda_item_id": "21/998/1", "position": n,
                                 "person_id": person, "speaker_name": person, "text": "w " * words,
                                 **prov}])  # fmt: skip
        if chair:
            upsert(conn, "speech_paragraph", [{"id": f"{sid}/1", "speech_id": sid, "position": 1, "kind": "chair",
                                               "text": chair}])  # fmt: skip
    conn.commit()
    ingest.ingest_speech_parts(conn)
    return {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT id, rede_id, interruption, interruption_start FROM speech WHERE sitting_id = '21/998'"
        )
    }


def test_speech_parts(store):
    main, asker, other = "11004006", "11003589", "11004004"
    parts = _rede_parts(store, "Gesetz über etwas", [
        ("R", main, 50, "Gestatten Sie eine Zwischenfrage?"),
        ("R-2", asker, 20, None),          # the question
        ("R-3", main, 5, None),            # "Bitte." - fewer than 30 words
        ("R-4", asker, 20, None),          # still the same question
        ("R-5", main, 40, "Es gibt den Wunsch nach einer Kurzintervention."),
        ("R-6", asker, 20, None),          # a new interruption, announced as a Kurzintervention
        ("R-7", other, 10, None),          # right after another interrupter: counts as a question
    ])  # fmt: skip
    assert parts == {
        "R": ("R", None, None), "R-2": ("R", "zwischenfrage", "R-2"), "R-3": ("R", None, None),
        "R-4": ("R", "zwischenfrage", "R-2"),  # the same question: one interruption over two parts
        "R-5": ("R", None, None), "R-6": ("R", "kurzintervention", "R-6"), "R-7": ("R", "zwischenfrage", "R-7"),
    }  # fmt: skip


def test_befragung_turns_are_no_interruptions(store):
    parts = _rede_parts(
        store, "Befragung der Bundesregierung", [("B", "11004006", 50, None), ("B-2", "11003589", 20, None)]
    )
    assert parts["B-2"] == ("B", None, None)
    assert store.execute("SELECT kind FROM agenda_item WHERE id = '21/998/1'").fetchone()[0] == "befragung"


def test_decision_vorgaenge_and_check(store):
    """A decision's Vorgänge: the roll call's, else DIP's step in the sitting, else every Vorgang of its Drucksache
    (a shared Unterrichtung left out); and the check against DIP."""
    prov = {"source_url": "u", "source_document_id": "d", "retrieved_at": "2026-01-01"}
    upsert(store, "sitting", [{"id": "21/998", "wahlperiode": 21, "number": 998, "date": "2026-01-01",
                               "xml_url": "x", "pdf_url": "p", **prov}])  # fmt: skip
    upsert(store, "vorgang", [{"id": v, "wahlperiode": 21, "title": f"Vorgang {v}", "subjects": "[]",
                               "initiators": "[]", **prov} for v in ("V1", "V2", "V3")])  # fmt: skip
    upsert(store, "drucksache", [
        {"id": "D1", "number": "21/9100", "wahlperiode": 21, "type": "Beschlussempfehlung und Bericht",
         "title": "BE", "date": "2026-01-01", "originators": "[]", "publisher": "BT", **prov},
        {"id": "D2", "number": "21/9200", "wahlperiode": 21, "type": "Unterrichtung", "title": "§ 80",
         "date": "2026-01-01", "originators": "[]", "publisher": "BT", **prov},
    ])  # fmt: skip
    upsert(
        store, "vorgang_drucksache", [{"vorgang_id": v, "drucksache_id": d} for v in ("V1", "V2") for d in ("D1", "D2")]
    )
    step = {"date": "2026-01-01", "position": "Beratung", "chamber": "BT", "document_kind": "Plenarprotokoll",
            "document_number": "21/998", "originators": "[]", **prov}  # fmt: skip
    upsert(store, "vorgang_position", [
        {"id": "P1", "vorgang_id": "V2", **step,
         "decisions": json.dumps([{"beschlusstenor": "Annahme der Vorlage", "dokumentnummer": "21/9100"}])},
        {"id": "P2", "vorgang_id": "V3", **step,  # a decision the protocol parser did not find
         "decisions": json.dumps([{"beschlusstenor": "Ablehnung der Vorlage", "dokumentnummer": "21/9300"}])},
    ])  # fmt: skip
    upsert(store, "roll_call_vote", [{"id": "21/998/1", "sitting_id": "21/998", "number": 1, "date": "2026-01-01",
                                      "title": "t", "vorgang_id": "V1", "yes": 1, "no": 0, "abstain": 0,
                                      "invalid": 0, "absent": 0, "xlsx_url": "x", **prov}])  # fmt: skip
    decision = {
        "sitting_id": "21/998",
        "kind": "handzeichen",
        "text": "t",
        "result": None,
        "roll_call_vote_id": None,
        **prov,
    }
    upsert(store, "decision", [
        {**decision, "id": "21/998/h1", "n": 1, "position": 1, "subject": "Antrag", "drucksache_number": "21/9100",
         "result": "abgelehnt"},  # DIP's step names it: V2 alone; DIP says Annahme
        {**decision, "id": "21/998/h2", "n": 2, "position": 2, "subject": "Bericht", "drucksache_number": "21/9200"},
        {**decision, "id": "21/998/1", "n": 1, "position": 3, "subject": "Antrag", "drucksache_number": "21/9100",
         "kind": "namentlich", "roll_call_vote_id": "21/998/1"},  # the roll call's V1 wins
    ])  # fmt: skip
    store.commit()
    ingest.ingest_vorlagen(store)
    links = {(r[0], r[1], r[2]) for r in store.execute("SELECT * FROM decision_vorgang")}
    assert {x for x in links if x[0].startswith("21/998")} == {
        ("21/998/h1", "V2", "dip_step"),
        ("21/998/1", "V1", "roll_call"),
    }
    rows = {r[0]: tuple(r[1:]) for r in store.execute(
        "SELECT id, vorgang_id, dip_position_id, dip_result FROM decision WHERE sitting_id = '21/998'")}  # fmt: skip
    assert rows["21/998/h1"] == ("V2", "P1", "Annahme der Vorlage")
    assert rows["21/998/h2"] == (None, None, None)  # the shared Unterrichtung ties it to nothing
    checks = [r for r in queries.decision_check(store) if r["sitting_id"] == "21/998"]
    assert [(r["kind"], r.get("decision_id") or r["drucksache_number"]) for r in checks] == [
        ("missing", "21/9300"),
        ("result", "21/998/h1"),
    ]
