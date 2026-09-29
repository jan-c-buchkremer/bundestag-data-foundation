"""Decisions from the chair's text. Every quoted paragraph is real, from the protocol named next to it."""

from xml.sax.saxutils import escape

import pytest

from bdf import parse_decisions, parse_protocol
from tests.conftest import FIXTURES

GRUENE, LINKE = "BÜNDNIS 90/DIE GRÜNEN", "Die Linke"


def _protocol(tmp_path, *paragraphs: str, top_id: str = "Tagesordnungspunkt 22", drucksachen: str = ""):
    """A protocol with one agenda item whose text outside speeches is the given chair paragraphs."""
    body = "".join(f'<p klasse="J">{escape(p)}</p>' for p in paragraphs)
    xml = (
        '<dbtplenarprotokoll wahlperiode="21" sitzung-nr="90" sitzung-datum="10.07.2026"><sitzungsverlauf>'
        f'<tagesordnungspunkt top-id="{top_id}"><p klasse="T_Drs">{drucksachen}</p>'
        f"<name>Präsidentin Julia Klöckner:</name>{body}</tagesordnungspunkt></sitzungsverlauf></dbtplenarprotokoll>"
    )
    path = tmp_path / "21090.xml"
    path.write_text(xml, encoding="utf-8")
    return parse_protocol.parse(path)


def _one(tmp_path, *paragraphs: str, **kw) -> parse_decisions.Decision:
    decisions = parse_decisions.extract(_protocol(tmp_path, *paragraphs, **kw))
    assert len(decisions) == 1, [d.subject for d in decisions]
    return decisions[0]


def test_result_sentence_with_zustimmung(tmp_path):
    d = _one(
        tmp_path,
        # 21/90, TOP 22
        "Der Ausschuss für Gesundheit empfiehlt in seiner Beschlussempfehlung auf Drucksache 21/7016, den "
        "Gesetzentwurf "
        "der Bundesregierung auf Drucksachen 21/6130 und 21/6559 in der Ausschussfassung anzunehmen. Hierzu liegt ein "
        "Änderungsantrag der Fraktion der AfD auf Drucksache 21/7017 vor, über den wir zuerst abstimmen. Wer stimmt "
        "dafür? – Wer stimmt dagegen? – Wer enthält sich? – Hiermit ist der Änderungsantrag mit den Stimmen der "
        "Koalitionsfraktionen, der Linken und der Fraktion Bündnis 90/Die Grünen abgelehnt bei Zustimmung der Fraktion "
        "der AfD.",
    )
    assert (d.id, d.kind, d.result, d.drucksache_number) == ("21/90/h1", "handzeichen", "abgelehnt", "21/7017")
    assert d.fractions == {"CDU/CSU": "no", "SPD": "no", LINKE: "no", GRUENE: "no", "AfD": "yes"}
    assert d.subject == "Änderungsantrag"


def test_mit_den_stimmen_gegen_die_stimmen(tmp_path):
    d = _one(
        tmp_path,
        # 21/90, TOP 22d
        "Tagesordnungspunkt 22d. Abstimmung über die Beschlussempfehlung des Ausschusses für Gesundheit zu dem Antrag "
        "der "
        "Fraktion Bündnis 90/Die Grünen mit dem Titel: „Psychotherapeutische Versorgung strukturell stärken“. Der "
        "Ausschuss empfiehlt unter Buchstabe c seiner Beschlussempfehlung auf Drucksache 21/6482, den Antrag der "
        "Fraktion Bündnis 90/Die Grünen auf Drucksache 21/4954 abzulehnen. Wer stimmt für diese Beschlussempfehlung "
        "des "
        "Ausschusses? – Wer stimmt dagegen? – Wer enthält sich? – Damit ist die Beschlussempfehlung des Ausschusses "
        "angenommen mit den Stimmen der Koalitionsfraktionen und der Fraktion der AfD gegen die Stimmen der Fraktionen "
        "Bündnis 90/Die Grünen und Die Linke.",
    )
    assert d.result == "angenommen" and d.drucksache_number == "21/6482"
    assert d.fractions == {"CDU/CSU": "yes", "SPD": "yes", "AfD": "yes", GRUENE: "no", LINKE: "no"}
    assert d.subject.startswith("Beschlussempfehlung des Ausschusses für Gesundheit zu dem Antrag der Fraktion")
    assert d.subject.endswith("„Psychotherapeutische Versorgung strukturell stärken“")


def test_einstimmig(tmp_path):
    # 21/89, TOP 29
    d = _one(
        tmp_path,
        "72 Petitionen. Wer stimmt dafür? – Auch hier ist das einstimmig. Sammelübersicht 283 ist damit angenommen.",
    )
    assert d.subject == "Sammelübersicht 283" and d.result == "angenommen"
    assert set(d.fractions.values()) == {"yes"} and len(d.fractions) == 5


def test_answers_across_paragraphs_and_rest_of_the_house(tmp_path):
    d = _one(
        tmp_path,
        # 21/89, TOP 9: the answer runs on in the next paragraph
        "Abstimmung über den Gesetzentwurf der Fraktion der AfD zur Änderung kindergeldrechtlicher Regelungen. Der "
        "Finanzausschuss empfiehlt unter Buchstabe b seiner Beschlussempfehlung auf Drucksache 21/6979, den "
        "Gesetzentwurf "
        "der Fraktion der AfD auf Drucksache 21/6003 abzulehnen. Ich bitte diejenigen, die dem Gesetzentwurf zustimmen "
        "wollen, um das Handzeichen. – Das ist die AfD-Fraktion. Wer stimmt dagegen? – Das ist der Rest des Hauses,",
        "die Fraktionen Die Linke, SPD, Bündnis 90/Die Grünen und CDU/CSU. Gibt es Enthaltungen? – Das ist nicht der "
        "Fall. Der Gesetzentwurf ist in zweiter Beratung abgelehnt. Damit entfällt nach unserer Geschäftsordnung die "
        "weitere Beratung.",
    )
    assert d.drucksache_number == "21/6003" and d.result == "abgelehnt"
    assert d.fractions == {"AfD": "yes", "CDU/CSU": "no", "SPD": "no", GRUENE: "no", LINKE: "no"}
    assert d.subject.endswith("– zweite Beratung")


def test_second_and_third_reading(tmp_path):
    decisions = parse_decisions.extract(
        _protocol(
            tmp_path,
            # 21/89, TOP 29
            "Der Ausschuss für Wirtschaft und Energie empfiehlt in seiner Beschlussempfehlung auf Drucksache 21/6995, "
            "den Gesetzentwurf der Bundesregierung auf Drucksache 21/5873 anzunehmen. Ich bitte jetzt diejenigen, die "
            "dem "
            "Gesetzentwurf zustimmen wollen, um das Handzeichen. – Das sind die Unionsfraktion und die SPD-Fraktion. "
            "Wer "
            "ist dagegen? – Das ist die Fraktion Die Linke. Wer enthält sich? – Das sind die AfD-Fraktion und die "
            "Fraktion Bündnis 90/Die Grünen. Der Gesetzentwurf ist damit in zweiter Beratung angenommen.",
            "Dritte Beratung",
            "und Schlussabstimmung. Ich bitte jetzt diejenigen, die dem Gesetzentwurf zustimmen wollen, sich zu "
            "erheben. "
            "– Das sind die Unionsfraktion und die SPD-Fraktion. Wer stimmt dagegen? – Das ist die Fraktion Die Linke. "
            "Wer enthält sich? – Das sind die AfD-Fraktion und die Fraktion Bündnis 90/Die Grünen. Der Gesetzentwurf "
            "ist "
            "damit angenommen.",
        )
    )
    second, third = decisions
    assert second.subject == "Gesetzentwurf – zweite Beratung" and third.subject == "Gesetzentwurf – Schlussabstimmung"
    assert second.drucksache_number == third.drucksache_number == "21/5873"
    expected = {"CDU/CSU": "yes", "SPD": "yes", LINKE: "no", "AfD": "abstain", GRUENE: "abstain"}
    assert second.fractions == third.fractions == expected
    assert [d.id for d in decisions] == ["21/90/h1", "21/90/h2"]


def test_summary_after_the_answers_does_not_override_them(tmp_path):
    d = _one(
        tmp_path,
        # 21/89, TOP 7
        "Unter Buchstabe b seiner Beschlussempfehlung auf Drucksache 21/6998 empfiehlt der Ausschuss, eine "
        "Entschließung "
        "anzunehmen. Wer stimmt für diese Beschlussempfehlung? – Die Unionsfraktion und die SPD-Fraktion. Wer stimmt "
        "dagegen? – Die AfD-Fraktion, Bündnis 90/Die Grünen und Die Linke.",
        "– Sie stimmen dagegen. Enthaltungen? – Dann fasse ich noch mal zusammen: Die Koalition hat für die "
        "Beschlussempfehlung gestimmt, AfD, Bündnis 90/Die Grünen und Die Linke dagegen. Damit ist die "
        "Beschlussempfehlung angenommen.",
    )
    assert d.fractions == {"CDU/CSU": "yes", "SPD": "yes", "AfD": "no", GRUENE: "no", LINKE: "no"}


def test_gegenprobe_and_short_answers(tmp_path):
    d = _one(
        tmp_path,
        # 21/90, TOP 22a
        "Ich komme zurück zum Tagesordnungspunkt 22a und bitte alle, die jetzt hereinkommen, an den Abstimmungen "
        "teilzunehmen. Wir setzen die Abstimmung über die Beschlussempfehlung des Ausschusses für Gesundheit auf "
        "Drucksache 21/7016 fort. Der Ausschuss empfiehlt unter Buchstabe b seiner Beschlussempfehlung, eine "
        "Entschließung anzunehmen. Wer stimmt für diese Beschlussempfehlung? – CDU/CSU und SPD. Gegenprobe! – AfD. "
        "Enthaltungen? – Bündnis 90/Die Grünen, Die Linke. Damit ist die Beschlussempfehlung angenommen.",
    )
    assert d.fractions == {"CDU/CSU": "yes", "SPD": "yes", "AfD": "no", GRUENE: "abstain", LINKE: "abstain"}


def test_empty_answer_and_result_in_next_paragraph(tmp_path):
    d = _one(
        tmp_path,
        # 21/89, ZP 7
        "So, dann setzen wir jetzt fort. Die Wahlvorschläge der Fraktion der Linken auf Drucksache 21/6895. Wer stimmt "
        "dafür? – Das sind die Fraktion Die Linke, die SPD, Bündnis 90/Die Grünen und die Unionsfraktion. Wer stimmt "
        "dagegen, demokratisch? – Das die AfD-Fraktion.",
        "Damit sind die Wahlvorschläge angenommen.",
    )
    assert (d.subject, d.drucksache_number, d.result) == ("Wahlvorschläge", "21/6895", "angenommen")
    assert d.fractions == {LINKE: "yes", "SPD": "yes", GRUENE: "yes", "CDU/CSU": "yes", "AfD": "no"}


def test_whole_house(tmp_path):
    d = _one(
        tmp_path,
        # 21/90, TOP 24
        "Zusatzpunkt 19. Wir kommen nun zur Abstimmung über den Antrag der Fraktionen der CDU/CSU und SPD auf "
        "Drucksache "
        "21/6911 mit dem Titel „Änderung der Geschäftsordnung des Deutschen Bundestages – hier: Anwesenheitserfassung "
        "an "
        "Sitzungstagen“. Wer stimmt für diesen Antrag? – Das ist das gesamte Haus. Wer stimmt dagegen? – Niemand. "
        "Enthaltungen gibt es auch nicht. Damit ist auch dieser Antrag angenommen.",
    )
    # a change of the Geschäftsordnung is a decision on substance, not procedure
    assert d.drucksache_number == "21/6911" and set(d.fractions.values()) == {"yes"} and len(d.fractions) == 5


@pytest.mark.parametrize(
    "paragraph",
    [
        # 21/88: removing an item from the agenda
        "Ich komme nun zur Abstimmung über den Absetzungsantrag der Fraktionen Bündnis 90/Die Grünen und Die Linke. "
        "Wer "
        "stimmt für die Absetzung von Tagesordnungspunkt 22a? – Wer stimmt dagegen? – Wer enthält sich? – Damit ist "
        "der "
        "Antrag mit der Mehrheit der Koalitionsfraktionen gegen die Stimmen der Oppositionsfraktionen abgelehnt "
        "worden.",
        # 21/89: disputed committee referral
        "Ich lasse jetzt zunächst einmal abstimmen über den Überweisungsvorschlag der Fraktion der AfD. Wer stimmt für "
        "diesen Vorschlag? – Das ist die AfD-Fraktion. Wer stimmt dagegen? – Das sind alle übrigen Fraktionen. "
        "Enthaltungen? – Sehe ich keine. Dann ist der Überweisungsvorschlag abgelehnt.",
        # 21/89: referral without a vote
        "Interfraktionell wird die Überweisung der Vorlagen auf den Drucksachen 21/6906 und 21/6907 an die in der "
        "Tagesordnung aufgeführten Ausschüsse vorgeschlagen. Sind Sie damit einverstanden? – Das ist so. Dann ist die "
        "Überweisung beschlossen.",
        # 21/65 (chair inside a speech in the original)
        "Nachdem die Zwischenfrage abgelehnt worden ist, hat der Kollege Dr. Kraft jetzt noch die Möglichkeit zu einer "
        "Kurzintervention.",
    ],
)
def test_procedure_is_not_a_decision(tmp_path, paragraph):
    assert parse_decisions.extract(_protocol(tmp_path, paragraph)) == []


def test_roll_call_opening_and_result(tmp_path):
    protocol = _protocol(
        tmp_path,
        # 21/89, TOP 8c: opened …
        "Tagesordnungspunkt 8c. Abstimmung über den Gesetzentwurf der Fraktion Bündnis 90/Die Grünen zur Änderung des "
        "Straßenverkehrsgesetzes (Tempolimit). Der Verkehrsausschuss empfiehlt in seiner Beschlussempfehlung auf "
        "Drucksache 21/6734, den Gesetzentwurf der Fraktion Bündnis 90/Die Grünen auf Drucksache 21/5319 abzulehnen. "
        "Die "
        "Fraktion Bündnis 90/Die Grünen hat namentliche Abstimmung beantragt.",
        "Ich eröffne somit die namentliche Abstimmung über den Gesetzentwurf auf Drucksache 21/5319. Die "
        "Abstimmungsurnen werden um 13 Uhr geschlossen. Das bevorstehende Ende der namentlichen Abstimmung wird Ihnen "
        "rechtzeitig bekannt gegeben.2Ergebnis Seite 10917 C",
        # … and its result read out later
        "Vielen Dank. – Bevor ich die nächste Rednerin aufrufe, gebe ich das von den Schriftführerinnen und "
        "Schriftführern ermittelte Ergebnis der namentlichen Abstimmung über den Gesetzentwurf zur Änderung des "
        "Straßenverkehrsgesetzes bekannt:",
        "Abgegebene Stimmen 604. Mit Ja haben gestimmt 137, mit Nein haben gestimmt 467. Es gab keine Enthaltungen. "
        "Der Gesetzentwurf ist damit in zweiter Beratung abgelehnt.",
        top_id="Tagesordnungspunkt 8 und Zusatzpunkt 4",
    )
    (d,) = parse_decisions.extract(protocol)
    assert (d.kind, d.drucksache_number, d.result, d.counts) == ("namentlich", "21/5319", "abgelehnt", (137, 467, 0))
    parse_decisions.link([d], [{"id": "21/90/1", "number": 1, "yes": 137, "no": 467, "drucksache_number": "21/5319"}])
    assert (d.id, d.roll_call_vote_id, d.n) == ("21/90/1", "21/90/1", 1)


@pytest.mark.parametrize(
    ("counts", "result", "expected"),
    [
        # 21/90
        ("Abgegebene Stimmkarten 561. Mit Ja haben gestimmt 518 Abgeordnete, mit Nein 43. Enthaltungen gab es keine.",
         "Damit ist der Gesetzentwurf angenommen.", ((518, 43, 0), "angenommen")),
        # 21/47
        ("Abgegebene Stimmkarten 586, Enthaltungen keine. Auf Ja entfielen 134 Stimmen, auf Nein 452 Stimmen.",
         "Damit ist der Antrag abgelehnt.", ((134, 452, 0), "abgelehnt")),
        # 21/48: one sentence per count
        ("Abgegebene Stimmkarten 583. Mit Ja haben gestimmt 453. Mit Nein haben gestimmt 130. 0 Enthaltungen.",
         "Damit ist die Beschlussempfehlung angenommen.", ((453, 130, 0), "angenommen")),
        # 21/40: majority of members required (Art. 87 Abs. 3 GG)
        ("Abgegebene Stimmkarten 579. Mit Ja haben gestimmt 322, mit Nein haben gestimmt 50, Enthaltungen gab es 207.",
         "Nach Artikel 87 Absatz 3 Satz 2 des Grundgesetzes ist zur Annahme dieses Gesetzes die Mehrheit der "
         "Mitglieder "
         "des Deutschen Bundestages erforderlich; das sind 316 Jastimmen. Der Gesetzentwurf hat damit die "
         "erforderliche "
         "Mehrheit erreicht.", ((322, 50, 207), "angenommen")),
    ],
)  # fmt: skip
def test_roll_call_result_phrasings(tmp_path, counts, result, expected):
    protocol = _protocol(
        tmp_path,
        "Ich eröffne die namentliche Abstimmung über den Gesetzentwurf auf Drucksache 21/1930.",
        "Ich gebe das von den Schriftführerinnen und Schriftführern ermittelte Ergebnis der namentlichen Abstimmung "
        "bekannt:",
        f"{counts} {result}",
    )
    (d,) = parse_decisions.extract(protocol)
    assert (d.counts, d.result) == expected


def test_election_results_are_not_roll_call_votes(tmp_path):
    protocol = _protocol(
        tmp_path,
        # 21/14
        "Bevor ich den nächsten Redner aufrufe, darf ich das von den Schriftführerinnen und Schriftführern ermittelte "
        "Ergebnis der Wahl eines Stellvertreters der Präsidentin des Deutschen Bundestages verkünden:",
        "Mitgliederzahl 630, abgegebene Stimmen 588, ungültige Stimmen 2. Mit Ja haben gestimmt 156 Abgeordnete, mit "
        "Nein haben gestimmt 416 Abgeordnete, Enthaltungen gab es 14.",
        "Der Abgeordnete Dr. Michael Kaufmann hat die erforderliche Mehrheit von mindestens 316 Stimmen nicht "
        "erreicht. "
        "Er ist damit nicht zum Stellvertreter der Präsidentin gewählt.",
    )
    assert parse_decisions.extract(protocol) == []


def test_opening_said_twice_is_one_vote(tmp_path):
    protocol = _protocol(
        tmp_path,
        # 21/68
        "Die Fraktion der AfD hat namentliche Abstimmung verlangt. Damit ist die namentliche Abstimmung über "
        "Buchstabe b "
        "der Beschlussempfehlung auf Drucksache 21/4984 eröffnet.",
        "Unter Buchstabe c empfiehlt der Ausschuss die Ablehnung des Antrags der Fraktion Die Linke auf Drucksache "
        "21/4751. Wer stimmt für diese Beschlussempfehlung? – Das sind die Koalitionsfraktionen und die AfD. Wer "
        "stimmt "
        "dagegen? – Die Linke. Wer enthält sich? – Bündnis 90/Die Grünen. Die Beschlussempfehlung ist angenommen.",
        "Damit haben wir jetzt diese Abstimmungen hinter uns gebracht, und die namentliche Abstimmung ist eröffnet.",
    )
    kinds = [d.kind for d in parse_decisions.extract(protocol)]
    assert kinds == ["namentlich", "handzeichen"]


def test_result_read_out_in_the_next_sitting(tmp_path):
    first = _protocol(
        tmp_path, "Ich eröffne die namentliche Abstimmung über die Beschlussempfehlung auf Drucksache 21/2117."
    )
    (opened,) = parse_decisions.extract(first)
    parse_decisions.link([opened], [{"id": "21/90/4", "number": 4, "yes": 494, "no": 72, "drucksache_number": None}])
    second = _protocol(
        tmp_path,
        "Ich gebe das Ergebnis der namentlichen Abstimmung von gestern bekannt: Mit Ja haben gestimmt 493, mit Nein "
        "haben "
        "gestimmt 72. Die Beschlussempfehlung ist damit angenommen.",
    )
    assert parse_decisions.extract(second, pending=[(opened, (494, 72))]) == []
    assert (opened.id, opened.result, opened.counts) == ("21/90/4", "angenommen", (493, 72, 0))


def test_fixture_excerpt_of_21_90():
    """ZP 28/29 of 21/90: the Gebäudeenergiegesetz vote (roll-call 21/90/7) and its show-of-hands neighbours."""
    protocol = parse_protocol.parse(FIXTURES / "decisions" / "21090.xml")
    decisions = parse_decisions.extract(protocol)
    parse_decisions.link(
        decisions, [{"id": "21/90/7", "number": 7, "yes": 323, "no": 271, "drucksache_number": "21/6278, 21/7009"}]
    )
    geg = next(d for d in decisions if d.roll_call_vote_id)
    assert (geg.id, geg.agenda_item_id, geg.result, geg.counts) == ("21/90/7", "21/90/1", "angenommen", (322, 272, 0))
    assert geg.subject.startswith("von der Bundesregierung eingebrachten Gesetzentwurf zur Änderung des Gebäude")
    by_id = {d.id: d for d in decisions}
    # ZP 20 is decided while TOP 25 is open; its Drucksache is on TOP 25's agenda
    assert by_id["21/90/h5"].agenda_item_id == "21/90/2" and by_id["21/90/h5"].drucksache_number == "21/6987"
    # the chair comes back to ZP 28a under TOP 25: the Entschließung of Drucksache 21/7009 belongs to ZP 28
    assert by_id["21/90/h6"].agenda_item_id == "21/90/1" and by_id["21/90/h6"].drucksache_number == "21/7009"
    assert by_id["21/90/h7"].fractions == {"AfD": "yes", "SPD": "no", GRUENE: "no", "CDU/CSU": "no", LINKE: "abstain"}


def test_ingest_decisions_and_vote_agenda_link(data_dir):
    import shutil
    import sqlite3

    from bdf import db, ingest, queries

    for name in ("21090.xml", "21090.xml.meta.json"):
        shutil.copy(FIXTURES / "decisions" / name, data_dir / "raw" / "bundestag" / "protocols" / "21" / name)
    # a store from before roll_call_vote.agenda_item_id existed is migrated on connect
    old = sqlite3.connect(data_dir / "bundestag.sqlite")
    old.execute("CREATE TABLE roll_call_vote (id TEXT PRIMARY KEY, sitting_id TEXT, number INTEGER NOT NULL, "
                "date TEXT NOT NULL, title TEXT NOT NULL, drucksache_number TEXT, vorgang_id TEXT, link_method TEXT, "
                "yes INTEGER NOT NULL, no INTEGER NOT NULL, abstain INTEGER NOT NULL, invalid INTEGER NOT NULL, "
                "absent INTEGER NOT NULL, xlsx_url TEXT NOT NULL, pdf_url TEXT, source_url TEXT NOT NULL, "
                "source_document_id TEXT NOT NULL, retrieved_at TEXT NOT NULL)")  # fmt: skip
    old.commit()
    old.close()
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    ingest.ingest_all(conn)  # idempotent

    vote = conn.execute("SELECT agenda_item_id FROM roll_call_vote WHERE id = '21/90/7'").fetchone()
    assert vote["agenda_item_id"] == "21/90/1"  # Drucksache 21/6278 is on the agenda of ZP 28, 29
    assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 10
    assert conn.execute("SELECT count(*) FROM decision WHERE source_document_id != 'BT-PlPr. 21/90'").fetchone()[0] == 0
    kinds = dict(conn.execute("SELECT kind, count(*) FROM agenda_item_paragraph GROUP BY kind").fetchall())
    assert kinds["chair"] > 20 and kinds["procedural"] > 5

    rows = queries.decisions(conn, "21/90")
    geg = next(r for r in rows if r["decision_id"] == "21/90/7")
    assert geg["roll_call"] == {"yes": 323, "no": 271, "abstain": 0} and geg["top_id"] == "Zusatzpunkt 28, 29"
    assert geg["source_url"] == "https://dserver.bundestag.de/btp/21/21090.xml"
    assert rows[0]["fractions"] == {"AfD": "no", GRUENE: "no", "CDU/CSU": "yes", LINKE: "no", "SPD": "yes"}


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        # 21/47, TOP 1, Schlussabstimmung (Linksfraktion)
        (
            "Der Gesetzentwurf ist mit den Stimmen der Koalitionsfraktionen bei Ablehnung von Bündnis 90/Die Grünen "
            "und der AfD-Fraktion und Enthaltung der Linksfraktion angenommen worden.",
            {"CDU/CSU": "yes", "SPD": "yes", GRUENE: "no", "AfD": "no", LINKE: "abstain"},
        ),
        # 21/56, TOP 34 (Grünenfraktion)
        (
            "Damit ist der Gesetzentwurf angenommen mit den Stimmen der Koalitionsfraktionen gegen die Stimmen der "
            "Fraktionen Die Linke und der AfD und bei Enthaltung der Grünenfraktion.",
            {"CDU/CSU": "yes", "SPD": "yes", LINKE: "no", "AfD": "no", GRUENE: "abstain"},
        ),
        # 21/14 (Linkspartei), 21/56 (Linkenfraktion)
        ("Der Antrag ist bei Zustimmung der Linkspartei angenommen worden.", {LINKE: "yes"}),
        ("Der Antrag ist bei Zustimmung der Linkenfraktion angenommen worden.", {LINKE: "yes"}),
    ],
)
def test_fraction_name_variants(result, expected):
    positions = parse_decisions.fraction_positions(result, result, "angenommen", 21)
    assert positions == expected
