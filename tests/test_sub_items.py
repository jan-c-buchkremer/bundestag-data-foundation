"""Sub-items of block agenda items, no-debate flags, Vorlagen and decisions attached to sub-items.

The fixtures are excerpts of real protocols (tests/fixtures/README.md): 21/96 TOP 41 (18 items 41b..41s voted
without debate, one speech) and TOP 8 (a debated pair), 21/50 TOP 37 (Portugal request, Sammelübersichten
117-142, Zusatzpunkte 10a-10o) and TOP 22 (a debated triple)."""

import json
import shutil

import pytest

from bdf import db, ingest, parse_decisions, parse_protocol
from bdf.parse_sub_items import call_up, says_no_debate
from tests.conftest import FIXTURES

LABELS_41 = [f"41{c}" for c in "bcdefghijklmnopqrs"]


@pytest.fixture(scope="module")
def p96():
    return parse_protocol.parse(FIXTURES / "sub_items" / "21096.xml")


@pytest.fixture(scope="module")
def p50():
    return parse_protocol.parse(FIXTURES / "sub_items" / "21050.xml")


def _subs(protocol, item_id):
    return [s for s in protocol.agenda_sub_items if s["agenda_item_id"] == item_id]


def test_call_up_paragraphs():
    assert call_up("Tagesordnungspunkt 41b:") == "41b"
    assert call_up("Zusatzpunkt 8:") == "ZP8"
    assert call_up("Zusatzpunkt 10a:") == "ZP10a"
    assert call_up("Wir kommen zu Tagesordnungspunkt 12k:") == "12k"
    assert call_up("Tagesordnungspunkt 20:") == "20"
    # aggregate call-ups and call-ups with words after them are not the start of a sub-item
    assert call_up("Ich rufe auf die Tagesordnungspunkte 41b bis 41s. Es handelt sich um …") is None
    assert call_up("Ich rufe auf die Tagesordnungspunkte 7a und 7b:") is None
    assert call_up("Tagesordnungspunkt 7a: Wir kommen zur Abstimmung über den Antrag") is None
    assert call_up("Tagesordnungspunkte 37b bis 37l sowie Zusatzpunkte 10a bis 10o. Wir kommen zu …") is None


def test_no_debate_phrases():
    for text in (
        "Es handelt sich um die Beschlussfassung zu Vorlagen, zu denen keine Aussprache vorgesehen ist.",
        "Eine Aussprache ist auch hier nicht vorgesehen.",
        "Es handelt sich um Überweisungen im vereinfachten Verfahren ohne Debatte.",
        "Der Bundestag hat über den Einspruch ohne Aussprache zu entscheiden.",
    ):
        assert says_no_debate(text), text
    for text in (
        "Ich eröffne die Aussprache.",
        "Weitere Reden zu dieser Debatte sind nicht vorgesehen.",
        "Eine Aussprache in der zweiten Beratung ist nicht vorgesehen.",
        "Das ist dann keine lebendige Debatte mehr.",
    ):
        assert not says_no_debate(text), text


def test_block_is_split_into_its_sub_items(p96):
    subs = _subs(p96, "21/96/1")
    assert [s["label"] for s in subs] == LABELS_41
    assert [s["id"] for s in subs][:2] == ["21/96/1/41b", "21/96/1/41c"]
    assert [s["position"] for s in subs] == list(range(1, 19))
    by = {s["label"]: s for s in subs}
    assert by["41b"]["title"] == (
        "Zweite und dritte Beratung des von der Bundesregierung eingebrachten Entwurfs eines Dritten Gesetzes "
        "zur Änderung des Seelotsgesetzes | Beschlussempfehlung und Bericht des Verkehrsausschusses (15. Ausschuss)"
    )
    assert json.loads(by["41b"]["drucksache_numbers"]) == ["21/6498", "21/8163"]
    assert json.loads(by["41d"]["drucksache_numbers"]) == ["21/1567", "21/2717", "21/5003"]
    assert by["41h"]["title"].endswith("Sammelübersicht 305 zu Petitionen")
    assert json.loads(by["41h"]["drucksache_numbers"]) == ["21/7955"]
    # the sub-items partition the Drucksachen of the item, which stays as it was
    item = p96.agenda_items[0]
    assert [n for s in subs for n in json.loads(s["drucksache_numbers"])] == json.loads(item["drucksache_numbers"])
    assert len(json.loads(item["drucksache_numbers"])) == 21
    assert item["title"].count(" | ") > 30
    # spans follow the chair's call-ups: first paragraph is the call-up itself
    paragraphs = {p["position"]: p["text"] for p in p96.agenda_paragraphs if p["agenda_item_id"] == "21/96/1"}
    assert paragraphs[by["41c"]["first_paragraph"]] == "Tagesordnungspunkt 41c:"
    assert by["41b"]["last_paragraph"] == by["41c"]["first_paragraph"] - 1


def test_zusatzpunkte_in_a_block(p50):
    subs = _subs(p50, "21/50/1")
    labels = [s["label"] for s in subs]
    assert labels == [f"37{c}" for c in "abcdefghijkl"] + [f"ZP10{c}" for c in "abcdefghijklmno"]
    by = {s["label"]: s for s in subs}
    assert by["37a"]["title"].startswith("Beratung des Antrags des Bundesministeriums der Finanzen | Vorzeitige")
    assert json.loads(by["37a"]["drucksache_numbers"]) == ["21/3143"]
    assert "Sammelübersicht 142 zu Petitionen" in by["ZP10o"]["title"]


def test_debated_lettered_items_are_not_split(p96, p50):
    # "Ich rufe nun auf die Tagesordnungspunkte 8a und 8b:" and later "Tagesordnungspunkt 8b: Wir kommen …"
    assert _subs(p96, "21/96/2") == []
    assert _subs(p50, "21/50/2") == []
    assert {s["agenda_item_id"] for s in p96.agenda_sub_items} == {"21/96/1"}


def test_no_debate_flags(p96, p50):
    assert [a["no_debate"] for a in p96.agenda_items] == [1, 0]
    assert [a["no_debate"] for a in p50.agenda_items] == [1, 0]
    assert all(s["no_debate"] == 1 for s in p96.agenda_sub_items + p50.agenda_sub_items)
    # the speech of the block stays a speech
    assert [s.agenda_item_id for s in p96.speeches if s.agenda_item_id == "21/96/1"] == ["21/96/1"]


def test_decisions_belong_to_the_sub_item_called_up_last(p96):
    decisions = parse_decisions.extract(p96)
    block = [d for d in decisions if d.agenda_item_id == "21/96/1"]
    assert len(block) == 20
    assert [d.sub_item_id.rsplit("/", 1)[1] for d in block] == [
        "41b", "41b", "41c", "41d", "41d", *LABELS_41[3:]
    ]  # fmt: skip
    assert [d.subject for d in block[:2]] == ["Gesetzentwurf – zweite Beratung", "Gesetzentwurf – Schlussabstimmung"]
    assert block[2].subject == "Antrag" and block[2].drucksache_number == "21/7910"
    # decisions of the debated item are not attached to anything
    assert [d.sub_item_id for d in decisions if d.agenda_item_id == "21/96/2"] == [None]


def test_sammelubersicht_numbers(p96, p50):
    block = [d for d in parse_decisions.extract(p96) if d.agenda_item_id == "21/96/1"]
    numbered = {d.sub_item_id.rsplit("/", 1)[1]: d for d in block if d.subject.startswith("Sammelübersicht")}
    assert sorted(numbered) == LABELS_41[5:]
    assert [d.subject for d in numbered.values()] == [f"Sammelübersicht {n}" for n in range(304, 317)]
    assert numbered["41h"].subject == "Sammelübersicht 305"  # the chair says "Auch diese Sammelübersicht …"
    assert numbered["41h"].drucksache_number == "21/7955"
    unnumbered = [
        d for d in parse_decisions.extract(p50) if d.subject.startswith("Sammelübersicht") and d.subject[-1] == "t"
    ]
    assert unnumbered == []


def test_decisions_of_the_second_block(p50):
    block = [d for d in parse_decisions.extract(p50) if d.agenda_item_id == "21/50/1"]
    assert block[0].sub_item_id == "21/50/1/37a" and block[0].subject == "Antrag"
    assert block[0].result == "angenommen"
    by = {d.sub_item_id.rsplit("/", 1)[1]: d.subject for d in block}
    assert by["37c"] == "Sammelübersicht 118" and by["ZP10o"] == "Sammelübersicht 142"


# --- the store ----------------------------------------------------------------------------------


@pytest.fixture
def blocks(data_dir):
    """The fixture store plus the two block protocols."""
    target = data_dir / "raw" / "bundestag" / "protocols" / "21"
    for name in ("21096.xml", "21096.xml.meta.json", "21050.xml", "21050.xml.meta.json"):
        shutil.copy(FIXTURES / "sub_items" / name, target / name)
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    return conn


def _dump(conn, *tables):
    return {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}")) for t in tables}


TABLES = ("agenda_item", "agenda_sub_item", "agenda_item_vorlage", "decision", "decision_fraction")


def test_store_rows(blocks):
    q = lambda sql: [tuple(r) for r in blocks.execute(sql)]  # noqa: E731
    assert q("SELECT count(*) FROM agenda_sub_item WHERE agenda_item_id = '21/96/1'") == [(18,)]
    assert q("SELECT count(*) FROM agenda_sub_item WHERE agenda_item_id = '21/50/1'") == [(27,)]
    assert q("SELECT count(*), sum(no_debate) FROM agenda_sub_item") == [(45, 45)]
    assert q("SELECT id, no_debate FROM agenda_item WHERE sitting_id IN ('21/96', '21/50') ORDER BY id") == [
        ("21/50/1", 1), ("21/50/2", 0), ("21/96/1", 1), ("21/96/2", 0),
    ]  # fmt: skip
    assert q("SELECT no_debate FROM agenda_item WHERE sitting_id = '21/94'") == [(0,)] * 2
    assert q("SELECT sub_item_id, subject, drucksache_number FROM decision WHERE id = '21/96/h9'") == [
        ("21/96/1/41h", "Sammelübersicht 305", "21/7955")
    ]
    assert q("SELECT count(*) FROM decision WHERE agenda_item_id = '21/96/1' AND sub_item_id IS NULL") == [(0,)]
    # Drucksachen of a block are listed per sub-item, none twice
    assert q("SELECT count(*), count(sub_item_id) FROM agenda_item_vorlage WHERE agenda_item_id = '21/96/1'") == [
        (21, 21)
    ]
    assert q("SELECT drucksache_number FROM agenda_item_vorlage WHERE sub_item_id = '21/96/1/41b' ORDER BY 1") == [
        ("21/6498",), ("21/8163",)
    ]  # fmt: skip
    # an item without sub-items lists its own Drucksachen
    assert q("SELECT drucksache_numbers FROM agenda_item WHERE id = '21/96/2'") == [
        ('["21/5322", "21/5650", "21/8115"]',)
    ]
    assert q("SELECT count(*) FROM agenda_item_vorlage WHERE agenda_item_id = '21/96/2' AND sub_item_id IS NULL") == [
        (3,)
    ]


def test_reingest_is_idempotent(blocks, data_dir):
    before = _dump(blocks, *TABLES)
    ingest.ingest_all(blocks)
    assert _dump(blocks, *TABLES) == before
    blocks.close()
    again = db.connect(data_dir / "bundestag.sqlite")  # a second process opening the store
    ingest.ingest_all(again)
    assert _dump(again, *TABLES) == before


def test_vorgang_links(blocks):
    prov = ("https://example.org", "test", "2026-09-30T00:00:00+00:00")

    def drucksache(number):
        blocks.execute(
            "INSERT INTO drucksache (id, number, wahlperiode, title, date, originators, source_url, "
            "source_document_id, retrieved_at) VALUES (?, ?, 21, 't', '2026-01-01', '[]', ?, ?, ?)",
            (f"d{number}", number, *prov),
        )

    def vorgang(vid, *numbers):
        blocks.execute(
            "INSERT INTO vorgang (id, wahlperiode, title, subjects, initiators, source_url, source_document_id, "
            "retrieved_at) VALUES (?, 21, 't', '[]', '[]', ?, ?, ?)",
            (vid, *prov),
        )
        for n in numbers:
            blocks.execute("INSERT INTO vorgang_drucksache VALUES (?, ?)", (vid, f"d{n}"))

    for n in ("21/7910", "21/6498", "21/7955"):
        drucksache(n)
    vorgang("v1", "21/7910", "21/7955")
    vorgang("v2", "21/6498")
    vorgang("v3", "21/6498")  # 21/6498 belongs to two Vorgänge: ambiguous, no link
    blocks.commit()
    ingest.ingest_vorlagen(blocks)
    links = dict(
        blocks.execute("SELECT drucksache_number, vorgang_id FROM agenda_item_vorlage WHERE vorgang_id IS NOT NULL")
    )
    assert links == {"21/7910": "v1", "21/7955": "v1"}
    assert dict(blocks.execute("SELECT id, vorgang_id FROM decision WHERE vorgang_id IS NOT NULL")) == {
        "21/96/h3": "v1",
        "21/96/h9": "v1",
    }
    ingest.ingest_vorlagen(blocks)  # again: same rows
    assert blocks.execute("SELECT count(*) FROM agenda_item_vorlage WHERE vorgang_id IS NOT NULL").fetchone()[0] == 2


def test_existing_store_is_migrated(blocks, data_dir):
    """A store built before the sub-item tables gets the new columns and tables on connect, and keeps its rows."""
    blocks.execute("DROP INDEX decision_sub_item")
    for table, column in (("decision", "sub_item_id"), ("decision", "vorgang_id"), ("agenda_item", "no_debate")):
        blocks.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    blocks.execute("DROP TABLE agenda_item_vorlage")
    blocks.execute("DROP TABLE agenda_sub_item")
    blocks.commit()
    n = blocks.execute("SELECT count(*) FROM decision").fetchone()[0]
    blocks.close()
    conn = db.connect(data_dir / "bundestag.sqlite")
    assert (
        conn.execute("SELECT count(*) FROM decision WHERE sub_item_id IS NULL AND vorgang_id IS NULL").fetchone()[0]
        == n
    )
    assert conn.execute("SELECT sum(no_debate) FROM agenda_item").fetchone()[0] == 0
    ingest.ingest_protocols(conn)
    ingest.ingest_decisions(conn)
    ingest.ingest_vorlagen(conn)
    assert conn.execute("SELECT count(*) FROM agenda_sub_item").fetchone()[0] == 45
    assert conn.execute("SELECT count(*) FROM decision WHERE sub_item_id IS NOT NULL").fetchone()[0] == 46
    assert conn.execute("SELECT sum(no_debate) FROM agenda_item").fetchone()[0] == 2


def test_sub_items_a_reparse_drops_go_away(blocks):
    """A sub-item that a changed parser no longer finds is deleted, with what points at it."""
    blocks.execute(
        "INSERT INTO agenda_sub_item (id, agenda_item_id, label, position, title, drucksache_numbers, "
        "first_paragraph, last_paragraph, source_url, source_document_id, retrieved_at) "
        "SELECT '21/96/1/41zz', agenda_item_id, '41zz', 99, NULL, '[]', 1, 2, source_url, source_document_id, "
        "retrieved_at FROM agenda_sub_item LIMIT 1"
    )
    blocks.execute("UPDATE decision SET sub_item_id = '21/96/1/41zz' WHERE id = '21/96/h1'")
    blocks.commit()
    ingest.ingest_protocols(blocks)
    assert blocks.execute("SELECT count(*) FROM agenda_sub_item WHERE label = '41zz'").fetchone()[0] == 0
    assert blocks.execute("SELECT sub_item_id FROM decision WHERE id = '21/96/h1'").fetchone()[0] is None
