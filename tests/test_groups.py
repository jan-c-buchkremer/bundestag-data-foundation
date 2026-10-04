"""Fraction vocabulary and speaker groups (bdf/names.py, ingest.ingest_groups)."""

import json

import pytest

from bdf import ingest
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
