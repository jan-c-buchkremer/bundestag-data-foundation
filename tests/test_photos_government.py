"""bundestag.de biography cards -> person_photo; Wikidata roster -> government_role (+ Commons portraits)."""

from bdf import ingest, parse_biografien, parse_wikidata, queries, raw
from tests.conftest import FIXTURES

PAGE = FIXTURES / "bundestag" / "biografien" / "page-000.html"
GOVERNMENT = FIXTURES / "wikidata" / "government.json"


def test_biography_cards():
    cards = {c.printed_name: c for c in parse_biografien.parse(PAGE.read_text(encoding="utf-8"))}
    assert len(cards) == 7
    bas = cards["Bas, Bärbel"]
    assert (bas.last_name, bas.first_name, bas.fraction) == ("Bas", "Bärbel", "SPD")
    assert bas.bio_url.startswith("https://www.bundestag.de/abgeordnete/biografien/B/bas_baerbel-")
    assert bas.image_url.startswith(f"https://www.bundestag.de/resource/image/{bas.image_id}/3x4/864/1152/")
    assert all(c.credit and not c.credit.startswith("©") for c in cards.values())
    assert cards["Santos-Wintz, Catarina dos"].last_name == "dos Santos-Wintz"
    assert cards["Meiser, Pascal"].fraction == "Die Linke"


def test_split_printed_names():
    assert parse_biografien.split_name("Kaufmann, Prof. Dr.-Ing. habil. Michael") == ("Kaufmann", "Michael")
    assert parse_biografien.split_name("Aken, Jan van") == ("van Aken", "Jan")
    assert parse_biografien.split_name("Schneider (Erfurt), Carsten") == ("Schneider", "Carsten")
    assert parse_biografien.split_name("Alhamwi, Dr. Alaa") == ("Alhamwi", "Alaa")


def test_person_photo_from_cards(store):
    rows = {r["person_id"]: r for r in store.execute("SELECT * FROM person_photo")}
    bas = rows["11004006"]
    assert bas["local_path"].startswith("bundestag/fotos/") and "/biografien/" in bas["bio_url"]
    assert bas["credit"] and bas["source_document_id"] == "bundestag.de Biografie Bas, Bärbel"
    assert "11004819" in rows  # Meiser
    # Aumer, the Beckers, dos Santos-Wintz match too, but their portraits are not in the fixtures
    assert "11004004" not in rows
    assert all((FIXTURES / r["local_path"]).exists() for r in rows.values())


def test_government_roles():
    roles = parse_wikidata.parse_roles(raw.read_json(GOVERNMENT))
    assert [(r.name, r.kind) for r in roles][:1] == [("Friedrich Merz", "kanzler")]
    warken = [r for r in roles if r.name == "Nina Warken"]
    assert {(r.office, r.from_date, r.to_date) for r in warken} == {
        ("Bundesminister für Gesundheit", "2025-05-06", "2026-07-29"),
        ("Chef des Bundeskanzleramtes", "2026-07-29", None),
    }
    bas = next(r for r in roles if r.name == "Bärbel Bas")
    assert (bas.department, bas.party, bas.birth_date) == (
        "Bundesministerium für Arbeit und Soziales",
        "SPD",
        "1968-05-03",
    )
    assert parse_wikidata.split_name("Reem Alabali Radovan", "Radovan") == ("Reem Alabali", "Radovan")


def test_government_ingest_matches_and_creates_persons(store):
    rows = {r["name"]: r for r in store.execute("SELECT * FROM government_role")}
    assert rows["Bärbel Bas"]["person_id"] == "11004006"  # QID from abgeordnetenwatch
    merz = store.execute("SELECT * FROM person WHERE id = 'Q566257'").fetchone()
    assert (merz["first_name"], merz["last_name"], merz["is_mdb"], merz["birth_date"]) == (
        "Friedrich", "Merz", 0, "1955-11-11",
    )  # fmt: skip
    assert rows["Friedrich Merz"]["kind"] == "kanzler"
    assert rows["Bärbel Bas"]["source_url"] == "https://www.wikidata.org/wiki/Q1019016#P39"
    # a non-MdB minister without a person row gets a Commons portrait with author and licence
    hubig = store.execute("SELECT * FROM person_photo WHERE person_id = 'Q20172451'").fetchone()
    assert hubig["credit"] == "Sandro Halank, Wikimedia Commons, CC BY-SA 4.0"
    assert hubig["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    # the MdB keeps the bundestag.de portrait
    assert store.execute("SELECT bio_url FROM person_photo WHERE person_id = '11004006'").fetchone()[0]


def test_government_name_match_replaces_a_made_up_person(store):
    """A protocol speaker without birth date (as non-MdB ministers are) is matched by name; the Q row goes."""
    with store:
        store.execute(
            "INSERT INTO person (id, first_name, last_name, is_mdb, role, source_url, source_document_id, "
            "retrieved_at) VALUES ('999990151', 'Stefanie', 'Hubig', 0, 'Bundesministerin der Justiz', 'u', 'd', 't')"
        )
    ingest.ingest_government(store)
    ingest.ingest_photos(store)
    role = store.execute("SELECT person_id FROM government_role WHERE wikidata_qid = 'Q20172451'").fetchone()
    assert role["person_id"] == "999990151"
    assert store.execute("SELECT wikidata_qid FROM person WHERE id = '999990151'").fetchone()[0] == "Q20172451"
    assert store.execute("SELECT 1 FROM person WHERE id = 'Q20172451'").fetchone() is None
    assert store.execute("SELECT 1 FROM person_photo WHERE person_id = '999990151'").fetchone()
    # the made-up id named her before (a consumer's page address): it leads to her now
    alias = store.execute("SELECT person_id FROM person_alias WHERE alias_id = 'Q20172451'").fetchone()
    assert alias["person_id"] == "999990151"


def test_a_pdf_speaker_named_in_the_final_xml_leaves_an_alias(store):
    """A speaker read from a PDF got "pdf-<name>"; once nothing refers to it, the row goes and an alias leads to
    the person of that name."""
    with store:
        store.execute(
            "INSERT INTO person (id, first_name, last_name, is_mdb, source_url, source_document_id, retrieved_at) "
            "VALUES ('pdf-baerbel-bas', 'Bärbel', 'Bas', 0, 'u', 'd', 't')"
        )
    ingest.retire_pdf_speakers(store)
    assert store.execute("SELECT 1 FROM person WHERE id = 'pdf-baerbel-bas'").fetchone() is None
    alias = store.execute("SELECT person_id FROM person_alias WHERE alias_id = 'pdf-baerbel-bas'").fetchone()
    assert alias["person_id"] == "11004006"


def test_government_query(store):
    today = queries.government(store, "2026-09-28")
    assert today[0]["kind"] == "kanzler"
    offices = {(r["name"], r["office"]) for r in today}
    assert ("Nina Warken", "Chef des Bundeskanzleramtes") in offices
    assert ("Nina Warken", "Bundesminister für Gesundheit") not in offices
    assert len(queries.government(store)) == 5
