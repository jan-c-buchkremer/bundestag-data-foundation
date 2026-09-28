"""Bundeswahlleiterin: parser details and the ingest into constituency, constituency_result, election_candidacy."""

from pathlib import Path

from bdf import parse_wahl

FIXTURES = Path(__file__).parent / "fixtures" / "bundeswahlleiterin" / "btw25"


def candidacy(store, last, first_names):
    return store.execute(
        "SELECT * FROM election_candidacy WHERE last_name = ? AND first_names = ?", (last, first_names)
    ).fetchone()


def test_read_table_skips_preamble_and_reads_stand():
    table = parse_wahl.read_table(FIXTURES / "btw25_gewaehlte_utf8.csv")
    assert table.as_of == "2025-03-12"
    assert len(table.rows) == 9 and table.rows[0]["Wahlart"] == "BT"
    assert parse_wahl.number("39,020282") == 39.020282 and parse_wahl.number("") is None


def test_constituency_winner_with_list_backup(store):
    bas = candidacy(store, "Bas", "Bärbel")
    assert bas["person_id"] == "11004006"
    assert (bas["elected_via"], bas["constituency_number"], bas["first_vote_percent"]) == (
        "constituency",
        114,
        39.020282,
    )
    assert (bas["list_state"], bas["list_position"]) == ("NW", 2)
    assert bas["source_document_id"] == "Bundeswahlleiterin, BTW 2025 Gewählte (Stand 2025-03-12)"
    assert bas["source_url"].endswith("btw25_gewaehlte_utf8.zip")


def test_list_member_and_the_wahlkreis_they_stood_in(store):
    becker = candidacy(store, "Becker", "Carsten Roland")
    assert becker["person_id"] == "11005412"  # first given name picks the right Becker
    assert (becker["elected_via"], becker["list_state"], becker["list_position"]) == ("list", "SL", 1)
    assert (becker["constituency_number"], becker["first_vote_percent"]) == (297, None)
    # the list member's own first-vote share is the party's Erststimme result in that Wahlkreis
    row = store.execute(
        "SELECT percent FROM constituency_result WHERE election = 'btw25' AND constituency_number = 297 "
        "AND party = 'AfD' AND vote = 1"
    ).fetchone()
    assert row["percent"] == 21.935194


def test_unknown_namesakes_stay_unmatched(store):
    assert candidacy(store, "Mayer", "Stephan")["person_id"] == "11003589"
    assert candidacy(store, "Mayer", "Andreas")["person_id"] is None
    assert candidacy(store, "Mayer", "Zoe")["person_id"] is None


def test_constituencies_and_seats(store):
    rows = {r["number"]: r for r in store.execute("SELECT * FROM constituency WHERE election = 'btw25'")}
    assert set(rows) == {14, 114, 297}
    assert (rows[114]["name"], rows[114]["state"], rows[114]["seat_party"]) == ("Duisburg I", "NW", "SPD")
    assert rows[14]["seat_party"] is None  # the winner got no seat (no Zweitstimmendeckung)
    assert rows[14]["state"] == "MV" and rows[14]["electorate"] == 221164
    assert (
        rows[14]["source_document_id"] == "Bundeswahlleiterin, BTW 2025 Ergebnisse nach Wahlkreisen (Stand 2025-03-14)"
    )


def test_winner_share_matches_results_table(store):
    """The two files agree: a constituency winner's share in Gewählte equals the party's Erststimme share."""
    for c in store.execute("SELECT * FROM election_candidacy WHERE elected_via = 'constituency'"):
        r = store.execute(
            "SELECT percent FROM constituency_result WHERE election = ? AND constituency_number = ? AND vote = 1 "
            "AND party = ?",
            (c["election"], c["constituency_number"], c["party"]),
        ).fetchone()
        if r is not None:  # only three Wahlkreise are in the fixture
            assert r["percent"] == c["first_vote_percent"]


def test_parse_gemeinden_builds_ags_and_flags_split():
    rows, as_of = parse_wahl.parse_gemeinden(FIXTURES / "btw25_wkr_gemeinden_20241130_utf8.csv")
    assert as_of == "2024-11-30"
    rostock = rows[0]
    assert (rostock["ags"], rostock["name"], rostock["state"], rostock["constituency_number"]) == (
        "13003000",
        "Rostock, Hanse- und Universitätsstadt",
        "MV",
        14,
    )
    assert rostock["split"] is False and rostock["district"]
    duisburg = next(r for r in rows if r["name"] == "Duisburg, Stadt")
    assert (duisburg["ags"], duisburg["split"]) == ("05112000", True)


def test_municipalities_one_row_per_wahlkreis(store):
    rows = store.execute(
        "SELECT * FROM constituency_municipality WHERE election = 'btw25' AND ags = '02000000' "
        "ORDER BY constituency_number"
    ).fetchall()
    assert [r["constituency_number"] for r in rows] == [18, 19]
    assert all(r["split"] == 1 and r["state"] == "HH" for r in rows)
    assert rows[0]["id"] == "btw25/02000000/18"
    assert rows[0]["source_document_id"] == "Bundeswahlleiterin, BTW 2025 Wahlkreiseinteilung (Stand 2024-11-30)"
    assert rows[0]["source_url"].endswith("btw25_wkr_gemeinden_20241130_utf8.csv")
    single = store.execute("SELECT * FROM constituency_municipality WHERE name = 'Beckingen'").fetchone()
    assert (single["constituency_number"], single["split"], single["state"]) == (297, 0, "SL")
