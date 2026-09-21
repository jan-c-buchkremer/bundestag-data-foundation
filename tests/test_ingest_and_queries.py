"""End to end on the fixtures: ingest everything, then answer the acceptance questions."""

import json

from bdf import queries
from bdf.match import PersonIndex

WEEK = ("2026-07-06", "2026-07-10")


def test_counts(store):
    counts = {
        t: store.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in (
            "person",
            "sitting",
            "agenda_item",
            "speech",
            "speech_paragraph",
            "roll_call_vote",
            "individual_vote",
            "drucksache",
            "drucksache_author",
            "vorgang",
            "vorgang_drucksache",
        )
    }
    assert counts["sitting"] == 1 and counts["agenda_item"] == 2 and counts["speech"] == 5
    assert counts["roll_call_vote"] == 1 and counts["individual_vote"] == 630
    assert counts["drucksache"] == 2 and counts["drucksache_author"] == 2 and counts["vorgang"] == 2
    assert counts["person"] == 11  # all fixture MdBs are Stammdaten records; no unknown speakers here


def test_every_fact_row_has_provenance(store):
    for table in (
        "person",
        "mandate",
        "membership",
        "sitting",
        "agenda_item",
        "speech",
        "drucksache",
        "drucksache_author",
        "vorgang",
        "roll_call_vote",
    ):
        bad = store.execute(
            f"SELECT count(*) FROM {table} WHERE source_url = '' OR source_document_id = '' OR retrieved_at = ''"
        ).fetchone()[0]
        assert bad == 0, table
    assert store.execute("SELECT source_document_id FROM speech LIMIT 1").fetchone()[0] == "BT-PlPr. 21/94"
    assert store.execute("SELECT source_document_id FROM roll_call_vote").fetchone()[0] == "NA 21/90/7"
    assert (
        store.execute("SELECT source_document_id FROM drucksache WHERE number='21/7022'").fetchone()[0]
        == "BT-Drs. 21/7022"
    )


def test_ingest_is_idempotent(store):
    from bdf import ingest

    before = store.execute("SELECT count(*) FROM speech_paragraph").fetchone()[0]
    ingest.ingest_all(store)
    assert store.execute("SELECT count(*) FROM speech_paragraph").fetchone()[0] == before
    assert store.execute("SELECT count(*) FROM individual_vote").fetchone()[0] == 630


def test_speeches_of_a_member(store):
    rows = queries.speeches(store, "11004004", "2026-09-11", "2026-09-11")
    assert [r["speech_id"] for r in rows] == ["ID219401000", "ID219401000-3"]
    assert rows[0]["top_id"] == "Einzelplan 11"
    assert rows[0]["source_url"] == "https://dserver.bundestag.de/btp/21/21094.xml"
    assert "Sehr geehrte Frau Präsidentin" in rows[0]["text"]


def test_votes_with_own_vote_and_fraction_line(store):
    rows = queries.votes(store, "11004819", *WEEK)  # Pascal Meiser
    assert len(rows) == 1
    v = rows[0]
    assert v["vote_id"] == "21/90/7" and v["own_vote"] == "no"
    assert v["fraction_line"]["fraction"] == "Die Linke" and v["fraction_line"]["majority"] == "no"
    assert v["result"] == {"yes": 323, "no": 271, "abstain": 0, "invalid": 0, "absent": 36}
    # linked through DIP's Vorgangsposition with abstimmungsart "Namentliche Abstimmung"
    assert (v["drucksache_number"], v["vorgang_id"], v["link_method"]) == (
        "21/6278",
        "400001",
        "dip_beschluss",
    )
    assert v["source_url"].endswith("20260710_7-xls.xlsx")


def test_vote_rows_are_matched_to_persons(store):
    unmatched = store.execute("SELECT count(*) FROM individual_vote WHERE person_id IS NULL").fetchone()[0]
    # the fixture Stammdaten holds 11 persons, so only those can match; all of them must
    matched = store.execute("SELECT person_id, last_name FROM individual_vote WHERE person_id IS NOT NULL").fetchall()
    assert {r["person_id"] for r in matched} >= {
        "11004006",
        "11004819",
        "11004004",
        "11003589",
        "11005198",
        "11005515",
    }
    assert unmatched == 630 - len(matched)
    # "Mayer (Altötting)" in the XLSX resolves to Stephan Mayer despite the Ortszusatz
    assert (
        store.execute("SELECT person_id FROM individual_vote WHERE last_name = 'Mayer (Altötting)'").fetchone()[0]
        == "11003589"
    )


def test_drucksachen_coauthored(store):
    rows = queries.drucksachen(store, "11004819", *WEEK)
    assert [r["number"] for r in rows] == ["21/7022"]
    assert rows[0]["type"] == "Entschließungsantrag" and rows[0]["activity_type"] == "Entschließungsantrag"
    assert rows[0]["source_document_id"] == "BT-Drs. 21/7022"
    assert json.loads(rows[0]["originators"]) == ["Fraktion Die Linke"]
    assert queries.drucksachen(store, "11004004", *WEEK) == []


def test_corpus_export(store):
    rows = queries.corpus(store, "2026-09-11", "2026-09-11")
    assert len(rows) == 5
    r = rows[0]
    assert (r["person_id"], r["is_mdb"], r["speaker_role"]) == (
        "11004006",
        1,
        "Bundesministerin für Arbeit und Soziales",
    )
    assert all(r["text"] and r["source_document_id"] == "BT-PlPr. 21/94" for r in rows)
    assert {r["fraction"] for r in rows} == {None, "Die Linke", "CDU/CSU"}


def test_cross_ids(store):
    p = dict(store.execute("SELECT * FROM person WHERE id = '11004819'").fetchone())
    assert p["dip_person_id"] == "5001"
    assert p["aw_politician_id"] is not None
    assert p["wikidata_qid"].startswith("Q")
    # abgeordnetenwatch's wrong ext ids (both Beckers -> 11000125) do not win over the name match
    assert store.execute("SELECT aw_politician_id FROM person WHERE id = '11000125'").fetchone()[0] is None
    assert (
        store.execute(
            "SELECT count(*) FROM person WHERE id IN ('11005412','11005411') AND aw_politician_id IS NOT NULL"
        ).fetchone()[0]
        == 2
    )
    # double surname on abgeordnetenwatch ("Labitzke Rathert") and "dos" prefix resolve
    assert store.execute("SELECT aw_politician_id FROM person WHERE id = '11005515'").fetchone()[0] is not None
    assert store.execute("SELECT aw_politician_id FROM person WHERE id = '11005198'").fetchone()[0] is not None


def test_person_index_ambiguity(store):
    index = PersonIndex(store, 21)
    assert index.match("Becker", "Carsten") == "11005412"
    assert index.match("Becker", "Desiree") == "11005411"
    assert index.match("Becker", "") is None  # two Beckers with a mandate: ambiguous
    assert index.match("Mayer (Altötting)", "Stephan") == "11003589"
    assert index.match("dos Santos-Wintz", "Catarina") == "11005198"
    assert index.match("Nobody", "At All") is None


def test_resolve_person_by_name(store):
    assert queries.resolve_person(store, "Bärbel Bas")["id"] == "11004006"
    assert queries.resolve_person(store, "11004819")["last_name"] == "Meiser"
