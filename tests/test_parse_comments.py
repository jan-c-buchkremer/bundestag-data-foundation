"""Comment paragraphs → interjections. Every input below is quoted from a WP 21 Plenarprotokoll."""

from bdf import ingest
from bdf import parse_comments as pc


def summary(comment):
    return [(p.kind, [(a.kind, a.fraction, a.name) for a in p.actors], p.text) for p in pc.parse(comment)]


def test_applause_by_fractions_and_by_some_members():
    assert summary("(Beifall bei der SPD sowie bei Abgeordneten des BÜNDNISSES 90/DIE GRÜNEN)") == [
        ("beifall", [("fraction", "SPD", None), ("members", "BÜNDNIS 90/DIE GRÜNEN", None)], None)
    ]
    assert summary("(Beifall beim BÜNDNIS 90/DIE GRÜNEN)") == [
        ("beifall", [("fraction", "BÜNDNIS 90/DIE GRÜNEN", None)], None)
    ]
    assert summary("(Anhaltender Beifall bei der LINKEN)") == [("beifall", [("fraction", "Die Linke", None)], None)]
    assert summary("(Beifall im ganzen Hause)") == [("beifall", [("house", None, None)], None)]


def test_named_members_inside_a_group():
    assert summary(
        "(Beifall bei der CDU/CSU sowie der Abg. Dr. Lars Castellucci [SPD] und "
        "Dr. Kirsten Kappert-Gonther [BÜNDNIS 90/DIE GRÜNEN])"
    ) == [
        (
            "beifall",
            [
                ("person", "SPD", "Dr. Lars Castellucci"),
                ("person", "BÜNDNIS 90/DIE GRÜNEN", "Dr. Kirsten Kappert-Gonther"),
                ("fraction", "CDU/CSU", None),
            ],
            None,
        )
    ]


def test_two_kinds_share_their_actors():
    assert [k for k, _, _ in summary("(Heiterkeit und Beifall bei Abgeordneten der CDU/CSU, der SPD)")] == [
        "heiterkeit",
        "beifall",
    ]


def test_named_interjections_split_only_where_a_new_part_starts():
    parts = summary(
        "(Beifall bei der SPD sowie bei Abgeordneten des BÜNDNISSES 90/DIE GRÜNEN – Zuruf von der AfD: Sind wir doch! "
        "– Sara Nanni [BÜNDNIS 90/DIE GRÜNEN], an die AfD gewandt: Hören Sie zu – bitte!)"
    )
    assert parts[1] == ("zuruf", [("fraction", "AfD", None)], "Sind wir doch!")
    assert parts[2] == ("zuruf", [("person", "BÜNDNIS 90/DIE GRÜNEN", "Sara Nanni")], "Hören Sie zu – bitte!")
    assert summary("(Claudia Roth [Augsburg] [BÜNDNIS 90/DIE GRÜNEN]: Was?)") == [
        ("zuruf", [("person", "BÜNDNIS 90/DIE GRÜNEN", "Claudia Roth")], "Was?")
    ]


def test_addressee_is_not_an_actor():
    (part,) = pc.parse("(Gegenruf des Abg. Stephan Brandner [AfD] an den Abg. Tilman Kuban [CDU/CSU]: Unsinn!)")
    assert [(a.kind, a.name) for a in part.actors] == [("person", "Stephan Brandner")]
    assert (part.kind, part.to.name, part.to.fraction, part.text) == ("gegenruf", "Tilman Kuban", "CDU/CSU", "Unsinn!")


def test_typos_and_leftovers():
    assert summary("(Zuruf der Abg. Lisa Badum [BÜNDNIS 90/DIE GÜNEN])")[0][1] == [
        ("person", "BÜNDNIS 90/DIE GRÜNEN", "Lisa Badum")
    ]
    assert summary("(Weiterer Gegenruf des Abg. Jörn König [AfD])")[0][:2] == (
        "gegenruf",
        [("person", "AfD", "Jörn König")],
    )
    assert summary("(Das Mikrofon wird abgeschaltet)") == [("other", [("unknown", None, None)], None)]
    assert summary("(Beifall)") == [("beifall", [("unknown", None, None)], None)]


def test_ingest_links_interjections_to_speech_and_person(store):
    rows = store.execute("SELECT * FROM interjection WHERE actor = 'person'").fetchall()
    meiser = [r for r in rows if r["name"] == "Pascal Meiser"]
    assert meiser and meiser[0]["person_id"] == "11004819" and meiser[0]["kind"] == "zuruf"
    assert meiser[0]["text"] == "Insgesamt sieht es aber schlecht aus!"
    applause = store.execute(
        "SELECT actor, fraction FROM interjection WHERE kind = 'beifall' AND fraction IS NOT NULL LIMIT 20"
    ).fetchall()
    assert ("members", "CDU/CSU") in [tuple(r) for r in applause]
    # every row points at a speech of the protocol, and re-ingesting replaces instead of adding
    n = store.execute("SELECT count(*) FROM interjection").fetchone()[0]
    ingest.ingest_protocols(store)
    assert store.execute("SELECT count(*) FROM interjection").fetchone()[0] == n
    assert (
        store.execute("SELECT count(*) FROM interjection WHERE speech_id NOT IN (SELECT id FROM speech)").fetchone()[0]
        == 0
    )
