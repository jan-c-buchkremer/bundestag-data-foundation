"""End to end on the fixtures: ingest everything, then answer the acceptance questions."""

import json

from bdf import ingest, queries
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
    assert counts["drucksache"] == 4 and counts["drucksache_author"] == 31 and counts["vorgang"] == 8
    # the 11 fixture MdBs are Stammdaten records, no unknown speakers here; plus 3 government members (Wikidata)
    assert counts["person"] == 14


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
        "vorgang_position",
        "roll_call_vote",
    ):
        bad = store.execute(
            f"SELECT count(*) FROM {table} WHERE source_url = '' OR source_document_id = '' OR retrieved_at = ''"
        ).fetchone()[0]
        assert bad == 0, table
    assert store.execute("SELECT source_document_id FROM speech LIMIT 1").fetchone()[0] == "BT-PlPr. 21/94"
    assert store.execute("SELECT source_document_id FROM roll_call_vote").fetchone()[0] == "NA 21/90/7"
    assert (
        store.execute("SELECT source_document_id FROM drucksache WHERE number='21/6977'").fetchone()[0]
        == "BT-Drs. 21/6977"
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
    # linked through DIP's Vorgangsposition with abstimmungsart "Namentliche Abstimmung";
    # for a bill DIP names the Gesetzentwurf and the Beschlussempfehlung
    assert (v["drucksache_number"], v["vorgang_id"], v["link_method"]) == (
        "21/6278, 21/7009",
        "334923",
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
    assert [r["number"] for r in rows] == ["21/6977"]
    assert rows[0]["type"] == "Entschließungsantrag" and rows[0]["activity_type"] == "Entschließungsantrag"
    assert rows[0]["source_document_id"] == "BT-Drs. 21/6977"
    assert json.loads(rows[0]["originators"]) == ["Fraktion DIE LINKE"]
    assert rows[0]["author_count"] == 21
    # a written question counts as authorship
    assert [(r["number"], r["activity_type"]) for r in queries.drucksachen(store, "11005505", *WEEK)] == [
        ("21/6977", "Entschließungsantrag"),
        ("21/7052", "Frage"),
    ]


def test_rapporteur_is_not_an_author(store):
    # Peter Aumer is Berichterstatter on the Beschlussempfehlung 21/7107: stored, but not authorship
    assert (
        store.execute(
            "SELECT activity_type FROM drucksache_author a JOIN drucksache d ON d.id = a.drucksache_id "
            "WHERE a.person_id = '11004004' AND d.number = '21/7107'"
        ).fetchone()[0]
        == "Berichterstattung"
    )
    assert queries.drucksachen(store, "11004004", *WEEK) == []


def test_author_count_falls_back_to_activities(store):
    # DIP reports autoren_anzahl 0 for Schriftliche Fragen; the fixture keeps 3 of its askers
    counts = dict(store.execute("SELECT number, author_count FROM drucksache").fetchall())
    assert counts["21/7052"] == 3
    assert counts["21/6977"] == 21  # agrees with DIP, kept


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
    assert p["dip_person_id"] == "2001"
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


def test_person_index_skips_pdf_placeholders(store):
    store.execute(
        "INSERT INTO person (id, first_name, last_name, is_mdb, source_url, source_document_id, retrieved_at)"
        " VALUES ('pdf-kristina-sinemus', 'Kristina', 'Sinemus', 0, 'x', 'x', 'x')"
    )
    assert PersonIndex(store, 21).match("Sinemus", "Kristina") is None


def test_resolve_person_by_name(store):
    assert queries.resolve_person(store, "Bärbel Bas")["id"] == "11004006"
    assert queries.resolve_person(store, "11004819")["last_name"] == "Meiser"


def test_overlapping_dip_ranges_count_each_record_once(data_dir):
    """`update` re-reads 14 days back, so a later range file repeats records of the earlier one."""
    from bdf import db, ingest

    dip = data_dir / "raw" / "dip"
    for kind in ("drucksache", "vorgangsposition"):
        src = dip / kind / "2026-07-06_2026-07-10.json"
        records = json.loads(src.read_text(encoding="utf-8"))
        if kind == "drucksache":
            records[0]["titel"] = "later title"  # the most recently retrieved copy wins
        (dip / kind / "2026-07-08_2026-07-22.json").write_text(json.dumps(records), encoding="utf-8")
        (dip / kind / "2026-07-08_2026-07-22.json.meta.json").write_text(
            json.dumps(
                {"url": "https://search.dip.bundestag.de/api/v1/overlap", "retrieved_at": "2026-09-28T03:40:00+00:00"}
            ),
            encoding="utf-8",
        )
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    # the vote's one DIP decision is not doubled, so it still pairs with the one vote of the day
    assert conn.execute("SELECT link_method FROM roll_call_vote WHERE id = '21/90/7'").fetchone()[0] == "dip_beschluss"
    assert conn.execute("SELECT count(*) FROM drucksache").fetchone()[0] == 4
    # 2 BT (deduplicated) + 2 BR from the untouched tests/fixtures/dip/vorgangsposition_other fixture
    assert conn.execute("SELECT count(*) FROM vorgang_position").fetchone()[0] == 4
    first = json.loads((dip / "drucksache" / "2026-07-06_2026-07-10.json").read_text(encoding="utf-8"))[0]
    assert conn.execute("SELECT title FROM drucksache WHERE id = ?", (first["id"],)).fetchone()[0] == "later title"


def test_vorgang_positions(store):
    rows = {r["id"]: dict(r) for r in store.execute("SELECT * FROM vorgang_position")}
    assert len(rows) == 4
    third = rows["696837"]
    assert (third["vorgang_id"], third["position"], third["chamber"], third["document_kind"]) == (
        "334923",
        "3. Beratung",
        "BT",
        "Plenarprotokoll",
    )
    assert third["pages"] == "11179" and third["ressort"] is None and third["originators"] == "[]"
    assert json.loads(third["decisions"])[0]["abstimmungsart"] == "Namentliche Abstimmung"
    question = rows["696051"]
    assert (question["document_number"], question["document_type"]) == ("21/6862", "Kleine Anfrage")
    assert json.loads(question["originators"]) == ["Fraktion der AfD"] and question["decisions"] is None
    assert question["source_document_id"] == "DIP Vorgangsposition 696051"
    assert question["source_url"] == "https://search.dip.bundestag.de/api/v1/vorgangsposition/696051"


def test_bundesrat_positions_from_the_separate_directory(store):
    """`vorgangsposition_other` (f.zuordnung=BR/BV/EK) merges into the same table as the BT positions."""
    rows = {r["id"]: dict(r) for r in store.execute("SELECT * FROM vorgang_position WHERE chamber = 'BR'")}
    assert set(rows) == {"700001", "700002"}
    assert all(r["vorgang_id"] == "334923" for r in rows.values())
    first_durchgang = rows["700001"]
    assert (first_durchgang["position"], first_durchgang["document_type"]) == (
        "Gesetzentwurf",
        "Gesetzentwurf der Bundesregierung",
    )
    assert json.loads(first_durchgang["originators"]) == ["Bundesregierung"]
    no_objection = rows["700002"]
    assert (no_objection["date"], no_objection["document_kind"]) == ("2026-07-17", "Plenarprotokoll")
    assert no_objection["source_document_id"] == "DIP Vorgangsposition 700002"


def test_vorgang_verkuendung_and_inkrafttreten(store):
    """The Verkündung/Ausfertigung (BGBl) and Inkrafttreten dates come from the /vorgang record itself,
    not from a vorgangsposition entry: no zuordnung ever carries a "Verkündung" vorgangsposition."""
    v = store.execute("SELECT * FROM vorgang WHERE id = '334923'").fetchone()
    assert v["status"] == "Verkündet"
    verkuendung = json.loads(v["verkuendung"])
    assert len(verkuendung) == 1
    assert (verkuendung[0]["ausfertigungsdatum"], verkuendung[0]["verkuendungsdatum"]) == (
        "2026-07-23",
        "2026-07-28",
    )
    assert verkuendung[0]["fundstelle"] == "BGBl I 2026, 226"
    inkrafttreten = json.loads(v["inkrafttreten"])
    assert [i["datum"] for i in inkrafttreten] == ["2026-07-29", "2027-01-01", "2028-01-01", "2030-01-01"]
    # a Vorgang without a Verkündung (not yet promulgated, or not a law) keeps NULL, not "[]"
    other = store.execute("SELECT verkuendung, inkrafttreten FROM vorgang WHERE id = '337023'").fetchone()
    assert other["verkuendung"] is None and other["inkrafttreten"] is None


def test_vorgang_position_ressort_and_page_range():
    from bdf import ingest, raw

    record = {
        "id": "1",
        "vorgang_id": "2",
        "datum": "2026-07-09",
        "vorgangsposition": "1. Beratung",
        "zuordnung": "BT",
        "fundstelle": {"dokumentart": "Plenarprotokoll", "dokumentnummer": "21/13", "anfangsseite": 12, "endseite": 15},
        "ressort": [{"federfuehrend": True, "titel": "Bundesministerium der Finanzen"}, {"titel": "Auswärtiges Amt"}],
    }
    row = ingest._vorgang_position_row(record, raw.RawMeta(url="u", retrieved_at="2026-09-27T00:00:00+00:00"))
    assert row["pages"] == "12-15"
    assert json.loads(row["ressort"]) == [
        {"titel": "Bundesministerium der Finanzen", "federfuehrend": True},
        {"titel": "Auswärtiges Amt", "federfuehrend": False},
    ]


def test_aw_profile_statistics(store):
    bas = store.execute("SELECT * FROM aw_profile WHERE person_id = '11004006'").fetchone()
    assert (bas["questions"], bas["questions_answered"]) == (689, 611)
    assert bas["url"] == "https://www.abgeordnetenwatch.de/profile/baerbel-bas"
    assert bas["source_document_id"] == f"aw politician {bas['aw_politician_id']}"
    assert bas["source_url"].startswith("https://www.abgeordnetenwatch.de/api/v2/politicians/")
    assert store.execute("SELECT count(*) FROM aw_profile").fetchone()[0] == 8


def test_side_jobs(store):
    rows = {r["id"]: r for r in store.execute("SELECT * FROM side_job")}
    assert len(rows) == 6
    bas = [r for r in rows.values() if r["person_id"] == "11004006"]
    assert len(bas) == 2 and {r["category"] for r in bas} == {
        "Berufliche Tätigkeit vor der Mitgliedschaft im Deutschen Bundestag"
    }
    board = rows[20565]
    assert board["organization"] == "Rhein-Maas Klinikum GmbH"
    assert board["income_level"] == 1 and board["income_range"] == "1.000 € bis 3.500 €"
    assert board["interval"] == "jährlich"
    assert board["source_document_id"] == "aw sidejob 20565"
    assert board["source_url"] == "https://www.abgeordnetenwatch.de/api/v2/sidejobs/20565"
    assert json.loads(rows[21007]["topics"]) and rows[21007]["created"].startswith("20")
    # a side job without a published Stufe keeps both fields empty
    assert rows[20670]["income_level"] is None and rows[20670]["income_range"] is None
    # re-ingest replaces, it does not duplicate
    ingest.ingest_side_jobs(store)
    assert store.execute("SELECT count(*) FROM side_job").fetchone()[0] == 6
