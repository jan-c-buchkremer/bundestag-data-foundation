"""government_role merged from Wikidata, Stammdaten memberships and the roles printed in the protocols."""

import sqlite3

from bdf import cli, db, government, ingest, queries
from bdf.government import Evidence, ParsedRole, parse_role

PROV = {"source_url": "u", "source_document_id": "d", "retrieved_at": "t"}


def test_parse_role_texts():
    assert parse_role("Bundesminister für Verkehr") == ParsedRole("minister", "Bundesministerium für Verkehr")
    assert parse_role("Bundesministerin der Justiz und für Verbraucherschutz") == ParsedRole(
        "minister", "Bundesministerium der Justiz und für Verbraucherschutz"
    )
    assert parse_role("Bundesminister des Auswärtigen") == ParsedRole("minister", "Auswärtiges Amt")
    assert parse_role("Bundesminister für besondere Aufgaben") == ParsedRole("minister", "Bundeskanzleramt")
    assert parse_role("Bundeskanzler") == ParsedRole("kanzler", "Bundeskanzleramt")
    assert parse_role(
        "Parl. Staatssekretärin bei der Bundesministerin für Bildung, Familie, Senioren, Frauen und Jugend"
    ) == ParsedRole("parl_sts", "Bundesministerium für Bildung, Familie, Senioren, Frauen und Jugend")
    assert parse_role("Parl. Staatssekretär beim Bundesminister der Finanzen") == ParsedRole(
        "parl_sts", "Bundesministerium der Finanzen"
    )
    assert parse_role("Staatsministerin beim Bundeskanzler") == ParsedRole("staatsminister", "Bundeskanzleramt")
    assert parse_role("Staatsminister beim Bundesminister des Auswärtigen") == ParsedRole(
        "staatsminister", "Auswärtiges Amt"
    )
    assert parse_role("Staatsminister für Kultur und Medien") == ParsedRole("staatsminister", "Bundeskanzleramt")
    assert parse_role("Staatssekretär im Bundesministerium der Finanzen") == ParsedRole(
        "beamteter_sts", "Bundesministerium der Finanzen"
    )
    # Stammdaten functions name no department (it is the institution)
    assert parse_role("Parlamentarische Staatssekretärin") == ParsedRole("parl_sts", None)
    assert parse_role("Staatsminister") == ParsedRole("staatsminister", None)
    # not federal government offices
    for text in ("Staatsminister (Hessen)", "Ministerpräsidentin (Mecklenburg-Vorpommern)",
                 "Beauftragte der Bundesregierung für Ostdeutschland", "Ordentliches Mitglied"):  # fmt: skip
        assert parse_role(text) is None, text


def test_department_key_tolerates_spelling():
    assert government.department_key("Bundesministerium der Justiz und Verbraucherschutz") == government.department_key(
        "Bundesministerium der Justiz und für Verbraucherschutz"
    )
    assert government.office_label("parl_sts", "Bundesministerium der Finanzen") == (
        "Parlamentarischer Staatssekretär beim Bundesminister der Finanzen"
    )
    assert government.office_label("staatsminister", "Auswärtiges Amt") == (
        "Staatsminister beim Bundesminister des Auswärtigen"
    )


def _e(source, pid, name, kind, dept, start, end=None, **kw) -> Evidence:
    prov = {**PROV, "source_document_id": kw.pop("doc", f"{source} doc")}
    return Evidence(source, pid, name, kind, dept, start, end, prov, **kw)


def test_merge_priority_and_inference():
    verkehr, gesundheit = "Bundesministerium für Verkehr", "Bundesministerium für Gesundheit"
    rows, inferences = government.merge(
        [
            _e("wikidata", "S", "Patrick Schnieder", "minister", verkehr, "2025-05-06", id="Q1-abc",
               office="Bundesminister für Verkehr", wikidata_qid="Q1"),
            _e("stammdaten", "S", "Patrick Schnieder", "minister", verkehr, "2025-05-06"),
            _e("protocol", "S", "Patrick Schnieder", "minister", verkehr, "2025-05-06", "2026-06-26"),
            _e("protocol", "B", "Steffen Bilger", "minister", verkehr, "2026-09-08", "2026-09-08",
               doc="BT-PlPr. 21/91"),
            # Wikidata has an end: nothing to infer, and its dates win over the open Stammdaten role
            _e("wikidata", "W", "Nina Warken", "minister", gesundheit, "2025-05-06", "2026-07-29", id="Q2-abc",
               office="Bundesminister für Gesundheit"),
            _e("stammdaten", "W", "Nina Warken", "minister", gesundheit, "2025-05-06"),
            _e("protocol", "L", "Carsten Linnemann", "minister", gesundheit, "2026-09-08", "2026-09-23"),
            # Stammdaten name spelled differently, protocol evidence of the same office
            _e("stammdaten", "K", "Anette Kramme", "parl_sts", "Bundesministerium der Justiz und Verbraucherschutz",
               "2025-05-06"),
            _e("protocol", "K", "Anette Kramme", "parl_sts", "Bundesministerium der Justiz und für Verbraucherschutz",
               "2026-03-25", "2026-06-26"),
            # a second Parl. Staatssekretär of a department closes nobody
            _e("protocol", "H", "Christian Hirte", "parl_sts", verkehr, "2026-01-16", "2026-01-16"),
        ]
    )  # fmt: skip
    by = {(r["person_id"], r["kind"]): r for r in rows}
    assert len(rows) == 6
    schnieder = by[("S", "minister")]
    assert (schnieder["id"], schnieder["source_kind"], schnieder["from_date"], schnieder["to_date"]) == (
        "Q1-abc", "wikidata", "2025-05-06", "2026-09-07",
    )  # fmt: skip
    assert [(i.name, i.to_date, i.successor, i.successor_document) for i in inferences] == [
        ("Patrick Schnieder", "2026-09-07", "Steffen Bilger", "BT-PlPr. 21/91")
    ]
    assert by[("W", "minister")]["to_date"] == "2026-07-29"
    bilger = by[("B", "minister")]
    assert bilger["id"] == "protocol:B:minister:bundesministerium-verkehr"
    assert (bilger["source_kind"], bilger["office"], bilger["source_document_id"]) == (
        "protocol", "Bundesminister für Verkehr", "BT-PlPr. 21/91",
    )  # fmt: skip
    kramme = by[("K", "parl_sts")]
    assert (kramme["source_kind"], kramme["from_date"], kramme["to_date"]) == ("stammdaten", "2025-05-06", None)
    # the department name comes from the protocol text, not the Stammdaten's spelling
    assert kramme["department"] == "Bundesministerium der Justiz und für Verbraucherschutz"
    assert kramme["id"] == "stammdaten:K:parl_sts:bundesministerium-der-justiz-verbraucherschutz"

    # held on a day: protocol evidence stays open after its last sitting until someone takes over
    today = {r["name"] for r in government.held_on(rows, "2026-09-28")}
    assert {"Steffen Bilger", "Carsten Linnemann", "Anette Kramme", "Christian Hirte"} <= today
    assert not {"Patrick Schnieder", "Nina Warken"} & today


def test_held_on_closes_protocol_evidence_at_a_successor():
    rows, _ = government.merge(
        [
            _e("protocol", "A", "A", "minister", "Bundesministerium für Verkehr", "2025-05-06", "2025-12-01"),
            _e("protocol", "B", "B", "minister", "Bundesministerium für Verkehr", "2026-01-15", "2026-02-01"),
        ]
    )
    assert [r["name"] for r in government.held_on(rows, "2025-12-20")] == ["A"]
    assert [r["name"] for r in government.held_on(rows, "2026-09-28")] == ["B"]


def test_ingest_merges_stammdaten_and_protocol_roles(store):
    bas = store.execute("SELECT * FROM government_role WHERE person_id = '11004006'").fetchall()
    assert len(bas) == 1  # Wikidata, Stammdaten and the protocol role, one row
    assert (bas[0]["source_kind"], bas[0]["id"][:9]) == ("wikidata", "Q1019016-")
    # Aumer (a speaker in the fixture protocol) as Parl. Staatssekretär: a Stammdaten membership and a speech role
    with store:
        store.execute(
            "INSERT INTO membership (id, person_id, wahlperiode, kind, name, role, from_date, to_date, source_url, "
            "source_document_id, retrieved_at) VALUES ('11004004/21/99', '11004004', 21, 'other', "
            "'Bundesministerium für Verkehr', 'Parlamentarischer Staatssekretär', '2025-05-06', NULL, 'zip', "
            "'MDB_STAMMDATEN 2026-04-29', 't')"
        )
        store.execute(
            "UPDATE speech SET speaker_role = 'Parl. Staatssekretär beim Bundesminister für Verkehr' "
            "WHERE person_id = '11004004'"
        )
        # Meiser speaks as Staatsminister without any other source
        store.execute(
            "UPDATE speech SET speaker_role = 'Staatsminister beim Bundesminister der Finanzen' "
            "WHERE person_id = '11004819'"
        )
    ingest.ingest_government(store)
    rows = {r["person_id"]: r for r in store.execute("SELECT * FROM government_role")}
    aumer = rows["11004004"]
    assert (aumer["source_kind"], aumer["kind"], aumer["from_date"], aumer["to_date"]) == (
        "stammdaten", "parl_sts", "2025-05-06", None,
    )  # fmt: skip
    assert aumer["office"] == "Parlamentarischer Staatssekretär beim Bundesminister für Verkehr"
    assert aumer["source_document_id"] == "MDB_STAMMDATEN 2026-04-29"
    meiser = rows["11004819"]
    assert (meiser["source_kind"], meiser["kind"], meiser["department"]) == (
        "protocol", "staatsminister", "Bundesministerium der Finanzen",
    )  # fmt: skip
    assert meiser["id"] == "protocol:11004819:staatsminister:bundesministerium-der-finanzen"
    sitting = store.execute("SELECT date FROM sitting").fetchone()["date"]
    assert (meiser["from_date"], meiser["to_date"]) == (sitting, sitting)
    assert meiser["source_document_id"] == "BT-PlPr. 21/94" and meiser["source_url"].endswith("21094.xml")
    sources = {r["source_kind"] for r in queries.government(store, "2026-09-28")}
    assert sources == {"wikidata", "stammdaten", "protocol"}


def test_old_government_role_table_is_migrated(data_dir):
    old = sqlite3.connect(data_dir / "bundestag.sqlite")
    old.execute(
        "CREATE TABLE government_role (id TEXT PRIMARY KEY, person_id TEXT, wikidata_qid TEXT NOT NULL, "
        "name TEXT NOT NULL, office TEXT NOT NULL, department TEXT, kind TEXT NOT NULL, from_date TEXT NOT NULL, "
        "to_date TEXT, source_url TEXT NOT NULL, source_document_id TEXT NOT NULL, retrieved_at TEXT NOT NULL)"
    )
    old.execute("CREATE INDEX government_role_person ON government_role(person_id)")
    old.execute(
        "INSERT INTO government_role VALUES ('Q1-x', NULL, 'Q1', 'N', 'Bundeskanzler', NULL, 'kanzler', "
        "'2025-05-06', NULL, 'u', 'd', 't')"
    )
    old.commit()
    old.close()
    conn = db.connect(data_dir / "bundestag.sqlite")
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(government_role)")}
    assert not cols["wikidata_qid"]["notnull"]
    row = conn.execute("SELECT * FROM government_role").fetchone()
    assert (row["id"], row["wikidata_qid"], row["source_kind"]) == ("Q1-x", "Q1", "wikidata")
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'government_role_person'").fetchone()
    db.connect(data_dir / "bundestag.sqlite")  # a second connect changes nothing


def test_stale_lists_protocol_roles_still_held_but_not_seen_for_90_days():
    rows, _ = government.merge(
        [
            _e("protocol", "A", "A", "staatsminister", "Bundeskanzleramt", "2025-05-14", "2026-05-07"),  # stale
            _e("protocol", "B", "B", "parl_sts", "Bundesministerium der Finanzen", "2025-06-01", "2026-06-25"),
            _e("protocol", "C", "C", "minister", "Bundesministerium für Verkehr", "2025-05-06", "2025-12-01"),
            _e("protocol", "D", "D", "minister", "Bundesministerium für Verkehr", "2026-09-08", "2026-09-08"),
            _e("stammdaten", "E", "E", "parl_sts", "Bundesministerium der Justiz", "2025-05-06", None),
        ]
    )
    stale = government.stale(rows, "2026-09-23")
    # B: exactly 90 days is not yet stale; C: succeeded by D, no longer held; E: not protocol-only
    assert [(r["name"], r["days_since_seen"], r["newest_sitting"]) for r in stale] == [("A", 139, "2026-09-23")]
    assert [r["name"] for r in government.stale(rows, "2026-09-23", days=80)] == ["A", "B"]
    # nothing is closed: the stale role is still held
    assert "A" in {r["name"] for r in government.held_on(rows, "2026-09-23")}


def _add_protocol_role(store, to_date: str) -> None:
    with store:
        store.execute(
            "INSERT INTO government_role (id, person_id, wikidata_qid, name, office, department, kind, from_date, "
            "to_date, source_kind, source_url, source_document_id, retrieved_at) VALUES "
            "('protocol:11004819:staatsminister:bundeskanzleramt', '11004819', NULL, 'Meiser', "
            "'Staatsminister beim Bundeskanzler', 'Bundeskanzleramt', 'staatsminister', '2025-05-14', ?, 'protocol', "
            "'https://dserver.bundestag.de/btp/21/21003.xml', 'BT-PlPr. 21/3', 't')",
            (to_date,),
        )


def test_query_stale_roles(store, capsys):
    newest = store.execute("SELECT MAX(date) FROM sitting").fetchone()[0]
    assert queries.stale_roles(store) == []
    _add_protocol_role(store, "2025-06-01")
    [row] = queries.stale_roles(store)
    assert (row["name"], row["source_kind"], row["to_date"], row["newest_sitting"]) == (
        "Meiser", "protocol", "2025-06-01", newest,
    )  # fmt: skip
    assert row["source_document_id"] == "BT-PlPr. 21/3"

    cli.main(["query", "stale-roles"])
    out = capsys.readouterr().out
    assert out.startswith("last seen 2025-06-01 (") and "Meiser (11004819)" in out and "BT-PlPr. 21/3" in out
    cli.main(["query", "stale-roles", "--json", "--days", "100000"])
    assert capsys.readouterr().out == ""
