from collections import Counter

from bdf import parse_protocol, parse_stammdaten, parse_votes
from bdf.fetch_bundestag import _parse_vote_list
from bdf.names import normalize_fraction, normalize_name
from bdf.raw import RawMeta
from tests.conftest import FIXTURES

PROTOCOL = FIXTURES / "bundestag" / "protocols" / "21" / "21094.xml"


def test_protocol_header_and_agenda():
    p = parse_protocol.parse(PROTOCOL)
    assert (p.sitting_id, p.date, p.start_time, p.end_time) == (
        "21/94",
        "2026-09-11",
        "09:00",
        "14:08",
    )
    assert p.document_id == "BT-PlPr. 21/94"
    assert [a["top_id"] for a in p.agenda_items] == ["Tagesordnungspunkt 3", "Einzelplan 11"]
    assert p.agenda_items[0]["title"].startswith("a) Erste Beratung des von der Bundesregierung")
    assert p.agenda_items[0]["drucksache_numbers"] == '["21/7300", "21/7650", "21/7301"]'


def test_protocol_speeches_split_per_speaker():
    p = parse_protocol.parse(PROTOCOL)
    ids = [s.id for s in p.speeches]
    assert ids == ["ID219400100", "ID219400200", "ID219401000", "ID219401000-2", "ID219401000-3"]
    aumer, meiser, aumer_again = p.speeches[2:]
    assert (aumer.speaker.id, aumer.speaker.fraction) == ("11004004", "CDU/CSU")
    assert (meiser.speaker.id, meiser.speaker.fraction) == ("11004819", "Die Linke")
    assert aumer_again.speaker.id == "11004004"
    assert meiser.text.startswith("Vielen Dank, Frau Präsidentin.")
    assert [s.position for s in p.speeches] == [1, 2, 3, 4, 5]
    assert all(s.agenda_item_id == "21/94/2" for s in p.speeches)


def test_protocol_paragraph_kinds_and_clean_text():
    p = parse_protocol.parse(PROTOCOL)
    aumer = p.speeches[2]
    kinds = Counter(k for k, _ in aumer.paragraphs)
    assert kinds["text"] > 5 and kinds["comment"] > 0 and kinds["chair"] > 0
    chair = [t for k, t in aumer.paragraphs if k == "chair"]
    assert chair[0] == "Vizepräsidentin Andrea Lindholz:"
    assert "Herr Kollege." in chair
    # clean text carries neither interjections nor chair remarks nor NBSPs
    assert "(Beifall" not in aumer.text
    assert "Vizepräsidentin" not in aumer.text
    assert "\xa0" not in aumer.text and " " not in aumer.text


def test_protocol_non_mdb_speaker_keeps_role():
    bas = parse_protocol.parse(PROTOCOL).speeches[0].speaker
    assert bas.id == "11004006"
    assert bas.fraction is None
    assert bas.role == "Bundesministerin für Arbeit und Soziales"
    assert bas.printed == "Bärbel Bas, Bundesministerin für Arbeit und Soziales"


def test_stammdaten():
    tables = parse_stammdaten.parse(
        FIXTURES / "bundestag" / "stammdaten" / "MDB_STAMMDATEN.XML",
        RawMeta("u", "2026-09-21T10:00:00+00:00"),
    )
    persons = {p["id"]: p for p in tables["person"]}
    bas = persons["11004006"]
    assert (bas["first_name"], bas["last_name"], bas["birth_date"], bas["party"]) == (
        "Bärbel",
        "Bas",
        "1968-05-03",
        "SPD",
    )
    assert bas["source_document_id"] == "MDB_STAMMDATEN 2026-04-29"
    assert persons["11005198"]["name_prefix"] == "dos"
    m = next(m for m in tables["mandate"] if m["id"] == "11004006/21")
    assert (m["from_date"], m["to_date"], m["mandate_type"], m["constituency_number"]) == (
        "2025-03-25",
        None,
        "Direktwahl",
        114,
    )
    fraction = [
        x
        for x in tables["membership"]
        if x["person_id"] == "11004006" and x["wahlperiode"] == 21 and x["kind"] == "fraction"
    ]
    assert fraction[0]["name"] == "SPD"
    committees = [
        x
        for x in tables["membership"]
        if x["person_id"] == "11004819" and x["wahlperiode"] == 21 and x["kind"] == "committee"
    ]
    assert committees and all(x["role"] for x in committees)


def test_votes_xlsx():
    rc = parse_votes.parse(FIXTURES / "bundestag" / "votes" / "20260710_7-xls.xlsx")
    assert rc.id == "21/90/7"
    assert len(rc.rows) == 630
    assert Counter(r.vote for r in rc.rows) == {"yes": 323, "no": 271, "absent": 36}
    assert normalize_fraction("BÜ90/GR") == "BÜNDNIS 90/DIE GRÜNEN"
    assert {r.fraction for r in rc.rows} == {
        "CDU/CSU",
        "SPD",
        "AfD",
        "BÜNDNIS 90/DIE GRÜNEN",
        "Die Linke",
        "fraktionslos",
    }


def test_vote_list_fragment_parsing():
    fragment = """
    <tr><td> 9. Juli 2026 </td><td><a href="https://x/blob/1/20260709_1.pdf"> <span> 09.07.2026: Tempolimit </span>
    <span role="img"></span></a> <a href="https://x/blob/2/20260709_1_xls.xlsx">XLSX</a></td></tr>
    <tr><td> 8. Mai 20626 </td><td><a href="https://x/blob/3/20260508_1.pdf"> <span> 08.05.20626:
    <span>Ablehnung eines Antrags</span> </span> <span role="img"></span></a>
    <a href="https://x/blob/4/20260508_1-xls.xlsx">XLSX</a></td></tr>
    <tr><td>no xlsx</td><td><a href="https://x/blob/5/20200101_1.pdf"><span> 01.01.2020: x </span>
    <span role="img"></span></a></td></tr>
    """
    rows = _parse_vote_list(fragment)
    assert [(r["date"], r["number"], r["title"]) for r in rows] == [
        ("2026-07-09", 1, "Tempolimit"),
        (
            "2026-05-08",
            1,
            "Ablehnung eines Antrags",
        ),  # date from the file name, not the typo'd label
    ]
    assert rows[0]["pdf_url"] == "https://x/blob/1/20260709_1.pdf"


def test_normalize_name():
    assert normalize_name("Dr. Bärbel Bas") == "baerbel bas"
    assert normalize_name("Cansın Köktürk") == normalize_name("Cansin Köktürk")
    assert normalize_name("Müller") == normalize_name("Mueller")


def test_speaker_with_merged_ids_and_doubled_names(tmp_path):
    """Upstream quirk seen in seven WP21 protocols (Svenja Schulze, MdB and minister)."""
    xml = (
        '<dbtplenarprotokoll wahlperiode="21" sitzung-nr="18" sitzung-datum="30.06.2025"><sitzungsverlauf>'
        '<tagesordnungspunkt top-id="Tagesordnungspunkt 1"><rede id="ID211802600">'
        '<p klasse="redner"><redner id="11005217 999990074"><name><vorname>SvenjaSvenja</vorname>'
        "<nachname>SchulzeSchulze</nachname><fraktion>SPDSPD</fraktion></name></redner>Svenja Schulze (SPD):</p>"
        '<p klasse="J_1">Danke.</p></rede></tagesordnungspunkt></sitzungsverlauf></dbtplenarprotokoll>'
    )
    path = tmp_path / "21018.xml"
    path.write_text(xml, encoding="utf-8")
    sp = parse_protocol.parse(path).speeches[0].speaker
    assert (sp.id, sp.first_name, sp.last_name, sp.fraction) == ("11005217", "Svenja", "Schulze", "SPD")
