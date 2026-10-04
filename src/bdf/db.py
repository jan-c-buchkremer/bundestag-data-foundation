"""SQLite schema and a generic upsert. See docs/design.md for the model."""

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path

PROVENANCE = "source_url TEXT NOT NULL, source_document_id TEXT NOT NULL, retrieved_at TEXT NOT NULL"

GOVERNMENT_ROLE = f"""CREATE TABLE IF NOT EXISTS government_role (
    id TEXT PRIMARY KEY,                -- wikidata: statement id of the "position held" (P39) claim;
                                        -- else "<source_kind>:<person_id>:<kind>:<department key>"
    person_id TEXT REFERENCES person(id),
    wikidata_qid TEXT,                  -- the holder's QID, when known
    name TEXT NOT NULL,                 -- the holder's Wikidata label, else first + last name of the person
    office TEXT NOT NULL,               -- Wikidata's position label, else a generic one, "Bundesminister der Finanzen"
    department TEXT,                    -- ministry or office, "Bundesministerium der Finanzen"
    kind TEXT NOT NULL,                 -- kanzler | minister | staatsminister | parl_sts | beamteter_sts
    from_date TEXT NOT NULL,            -- wikidata, stammdaten: start of office; protocol: first sitting that
                                        -- prints the role for the person (evidence, not the appointment)
    to_date TEXT,                       -- wikidata, stammdaten: end of office (or inferred, docs/decisions.md);
                                        -- protocol: last sitting that prints the role (evidence, not the end)
    {PROVENANCE},
    source_kind TEXT NOT NULL DEFAULT 'wikidata'  -- wikidata | stammdaten | protocol: source of dates + provenance
);
"""

GOVERNMENT_ROLE_INDEX = "CREATE INDEX IF NOT EXISTS government_role_person ON government_role(person_id)"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS person (
    id TEXT PRIMARY KEY,                -- Bundestag MdB id (MDB/ID == redner/@id)
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    name_prefix TEXT,                   -- Adel / Präfix ("von", "Freiherr von")
    academic_title TEXT,
    birth_date TEXT,                    -- ISO date
    birth_place TEXT,
    gender TEXT,
    party TEXT,
    is_mdb INTEGER NOT NULL DEFAULT 1,
    role TEXT,                          -- for non-MdB speakers: rolle_lang from the protocol
    dip_person_id TEXT,
    aw_politician_id INTEGER,
    wikidata_qid TEXT,
    {PROVENANCE},
    fraction TEXT                       -- derived: the fraction in the newest Wahlperiode the person has a mandate in
                                        -- (the open membership, else the last one; without one, as for a Nachrücker
                                        -- not yet in the Stammdaten, the one printed in protocols and vote lists,
                                        -- else "fraktionslos"); NULL for everyone else
);

CREATE TABLE IF NOT EXISTS mandate (
    id TEXT PRIMARY KEY,                -- "<person_id>/<wp>"
    person_id TEXT NOT NULL REFERENCES person(id),
    wahlperiode INTEGER NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT,
    mandate_type TEXT,                  -- Direktwahl | Landesliste
    constituency_number INTEGER,
    constituency_name TEXT,
    state TEXT,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS membership (
    id TEXT PRIMARY KEY,                -- "<person_id>/<wp>/<n>"
    person_id TEXT NOT NULL REFERENCES person(id),
    wahlperiode INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- fraction | committee | other
    name TEXT NOT NULL,                 -- normalised fraction, or institution name
    role TEXT,                          -- FKT_LANG, e.g. "Vorsitzende"
    from_date TEXT,
    to_date TEXT,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS sitting (
    id TEXT PRIMARY KEY,                -- "21/94"
    wahlperiode INTEGER NOT NULL,
    number INTEGER NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    xml_url TEXT NOT NULL,
    pdf_url TEXT NOT NULL,
    {PROVENANCE},
    preliminary INTEGER NOT NULL DEFAULT 0,  -- 1: the XML is the preliminary version ("Der gesamte und damit
                                        -- endgültige Stenografische Bericht … wird am … veröffentlicht"); it can
                                        -- lack the last pages. `update` re-fetches it until the final one is served
    final_announced TEXT,               -- the date the preliminary XML announces for the final version, ISO
    final_fetched_at TEXT,              -- retrieved_at of the XML once it is final; NULL while preliminary
    first_page INTEGER,                 -- Druckseite the XML's sitzungsverlauf starts on (start-seitennr)
    last_page INTEGER                   -- preliminary XML: the Druckseite its text ends on, found in the final PDF,
                                        -- which supplies the rest of the sitting (rows with the PDF as source_url);
                                        -- NULL for a final XML and while no PDF part could be read
);

CREATE TABLE IF NOT EXISTS agenda_item (
    id TEXT PRIMARY KEY,                -- "<sitting_id>/<position>"
    sitting_id TEXT NOT NULL REFERENCES sitting(id),
    position INTEGER NOT NULL,
    top_id TEXT NOT NULL,               -- XML top-id attribute, e.g. "Tagesordnungspunkt 3"
    title TEXT,
    drucksache_numbers TEXT NOT NULL,   -- JSON array of "21/7300"
    {PROVENANCE},
    no_debate INTEGER NOT NULL DEFAULT 0  -- 1: the chair says no Aussprache is provided ("keine Aussprache vorgesehen")
);

CREATE TABLE IF NOT EXISTS agenda_sub_item (
    id TEXT PRIMARY KEY,                -- "<agenda_item_id>/<label>", e.g. "21/96/6/41b"
    agenda_item_id TEXT NOT NULL REFERENCES agenda_item(id),
    label TEXT NOT NULL,                -- as called up: "41b"; Zusatzpunkte "ZP8", "ZP1a"
    position INTEGER NOT NULL,          -- order within the agenda item
    title TEXT,                         -- this sub-item's title lines, joined with " | "
    drucksache_numbers TEXT NOT NULL,   -- JSON array of "21/7300", this sub-item's only
    first_paragraph INTEGER NOT NULL,   -- agenda_item_paragraph.position of the chair's call-up
    last_paragraph INTEGER NOT NULL,    -- … of the last paragraph before the next call-up (or the item's end)
    {PROVENANCE},
    no_debate INTEGER NOT NULL DEFAULT 0  -- 1: the chair says no Aussprache is provided (for the block or this item)
);

CREATE TABLE IF NOT EXISTS agenda_item_vorlage (
    id TEXT PRIMARY KEY,                -- "<agenda_item_id or sub_item_id>/<drucksache_number>"
    agenda_item_id TEXT NOT NULL REFERENCES agenda_item(id),
    sub_item_id TEXT REFERENCES agenda_sub_item(id),  -- set for the Drucksachen of a sub-item, NULL otherwise
    drucksache_number TEXT NOT NULL,    -- "21/7300"
    vorgang_id TEXT REFERENCES vorgang(id),  -- the only Vorgang of that Drucksache in vorgang_drucksache; else NULL
    {PROVENANCE},
    via TEXT NOT NULL DEFAULT 'title'   -- title: the item's printed title names it; decision: a decision taken under
                                        -- the item names it (an Entschließungsantrag decided after the bill)
);

CREATE TABLE IF NOT EXISTS speech (
    id TEXT PRIMARY KEY,                -- XML rede/@id, "-2", "-3" … when split; Fragestunde:
                                        -- "<agenda_item_id>/f<n>" (synthetic, docs/decisions.md)
    sitting_id TEXT NOT NULL REFERENCES sitting(id),
    agenda_item_id TEXT REFERENCES agenda_item(id),
    position INTEGER NOT NULL,          -- order within the sitting
    person_id TEXT NOT NULL REFERENCES person(id),
    speaker_name TEXT NOT NULL,
    speaker_role TEXT,
    fraction TEXT,
    text TEXT NOT NULL,                 -- clean text: paragraphs of kind 'text', blank-line joined
    {PROVENANCE},
    kind TEXT NOT NULL DEFAULT 'rede',  -- rede | fragestunde: a Fragestunde question, answer or
                                        -- Nachfrage; shown, but left out of speech counts and shares
    sub_item_id TEXT REFERENCES agenda_sub_item(id),  -- the sub-item of a block item during which the speech was
                                        -- given (its call-up is the last one before the speech), else NULL
    speaker_group TEXT,                 -- derived: who the speech counts for: a fraction | Bundesregierung (given in a
                                        -- federal government office, whatever the speaker's fraction) | Bundesrat
                                        -- (a Land office) | Sonstige
    member_fraction TEXT                -- derived: the speaker's fraction on the sitting day (membership, else the
                                        -- printed one), also when speaking as a minister; NULL for non-members
);

CREATE TABLE IF NOT EXISTS speech_paragraph (
    id TEXT PRIMARY KEY,                -- "<speech_id>/<position>"
    speech_id TEXT NOT NULL REFERENCES speech(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- text | comment | chair | procedural
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interjection (
    id TEXT PRIMARY KEY,                -- "<speech_id>/<paragraph>/<part>/<actor>"
    speech_id TEXT NOT NULL REFERENCES speech(id),
    paragraph INTEGER NOT NULL,         -- speech_paragraph.position of the comment
    part INTEGER NOT NULL,              -- order of the part within the comment
    kind TEXT NOT NULL,                 -- beifall | zuruf | gegenruf | lachen | heiterkeit | widerspruch
                                        -- | zustimmung | unruhe | other
    actor TEXT NOT NULL,                -- fraction | members (some of the fraction) | person | house | unknown
    fraction TEXT,
    person_id TEXT REFERENCES person(id),
    name TEXT,                          -- printed name of a person
    text TEXT,                          -- the words of a Zuruf / Gegenruf, when printed
    to_person_id TEXT REFERENCES person(id),  -- addressee named in the comment ("an den Abg. …");
    to_name TEXT                        -- NULL: aimed at the speaker of speech_id
);

CREATE TABLE IF NOT EXISTS drucksache (
    id TEXT PRIMARY KEY,                -- DIP id
    number TEXT NOT NULL,               -- "21/7300"
    wahlperiode INTEGER NOT NULL,
    type TEXT,                          -- drucksachetyp
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    pdf_url TEXT,
    publisher TEXT,                     -- herausgeber: BT | BR
    originators TEXT NOT NULL,          -- JSON array of urheber titles
    author_count INTEGER,
    {PROVENANCE},
    originator_groups TEXT              -- derived: JSON array of the fractions and "Bundesregierung" among the
                                        -- originators (a ministry counts as the Bundesregierung); [] for none
);

CREATE TABLE IF NOT EXISTS drucksache_author (
    id TEXT PRIMARY KEY,                -- "<drucksache_id>/<dip_person_id>"
    drucksache_id TEXT NOT NULL REFERENCES drucksache(id),
    dip_person_id TEXT NOT NULL,
    person_id TEXT REFERENCES person(id),
    name TEXT NOT NULL,
    activity_type TEXT,                 -- aktivitaetsart, e.g. "Antrag", "Kleine Anfrage"
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS question_activity (
    id TEXT PRIMARY KEY,                -- DIP Aktivität id
    vorgang_id TEXT NOT NULL,           -- the single question (DIP vorgangsbezug), a Vorgang of question_type
    question_type TEXT NOT NULL,        -- Schriftliche Frage | Mündliche Frage
    activity_type TEXT NOT NULL,        -- Frage | Zusatzfrage | Antwort
    dip_person_id TEXT NOT NULL,
    person_id TEXT REFERENCES person(id),
    name TEXT NOT NULL,                 -- DIP's title, "Julia Schneider, MdB, BÜNDNIS 90/DIE GRÜNEN"
    ressort TEXT,                       -- Antwort: the answering ministry as the title names it, else NULL
    document_kind TEXT NOT NULL,        -- Drucksache (the Sammeldrucksache) | Plenarprotokoll
    document_number TEXT NOT NULL,      -- "21/4372", "21/9"
    question_numbers TEXT,              -- Drucksache: DIP's frage_nummer, "93, 94"
    page TEXT,                          -- Plenarprotokoll: page and quadrant, "683D"
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS vorgang (
    id TEXT PRIMARY KEY,                -- DIP id
    wahlperiode INTEGER NOT NULL,
    type TEXT,
    title TEXT NOT NULL,
    status TEXT,                        -- beratungsstand
    subjects TEXT NOT NULL,             -- JSON array (sachgebiet)
    initiators TEXT NOT NULL,           -- JSON array (initiative)
    verkuendung TEXT,                   -- JSON array of DIP verkuendung objects (BGBl reference), NULL if none
    inkrafttreten TEXT,                 -- JSON array of {{datum, erlaeuterung}} objects, NULL if none
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS vorgang_drucksache (
    vorgang_id TEXT NOT NULL REFERENCES vorgang(id),
    drucksache_id TEXT NOT NULL REFERENCES drucksache(id),
    PRIMARY KEY (vorgang_id, drucksache_id)
);

CREATE TABLE IF NOT EXISTS vorgang_position (
    id TEXT PRIMARY KEY,                -- DIP id of the Vorgangsposition (one step of a Vorgang)
    vorgang_id TEXT NOT NULL,           -- DIP id of the Vorgang; not every Vorgang is in table vorgang
    date TEXT NOT NULL,                 -- datum
    position TEXT NOT NULL,             -- vorgangsposition, e.g. "Gesetzentwurf", "1. Beratung", "Antwort"
    chamber TEXT,                       -- zuordnung: BT | BR | BV | …
    document_kind TEXT,                 -- fundstelle.dokumentart: Drucksache | Plenarprotokoll
    document_number TEXT,               -- fundstelle.dokumentnummer: "21/304" (Drucksache), "21/13" (protocol)
    document_type TEXT,                 -- fundstelle.drucksachetyp
    pdf_url TEXT,                       -- fundstelle.pdf_url
    pages TEXT,                         -- "1234-1236" (fundstelle.anfangsseite-endseite), protocols only
    originators TEXT NOT NULL,          -- JSON array of urheber titles
    ressort TEXT,                       -- JSON array of objects {{titel, federfuehrend}}, NULL if none
    decisions TEXT,                     -- JSON array of beschlussfassung objects as in DIP, NULL if none
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS roll_call_vote (
    id TEXT PRIMARY KEY,                -- "21/90/7"
    sitting_id TEXT REFERENCES sitting(id),
    number INTEGER NOT NULL,            -- Abstimmnr
    date TEXT NOT NULL,
    title TEXT NOT NULL,
    drucksache_number TEXT,
    vorgang_id TEXT REFERENCES vorgang(id),
    link_method TEXT,                   -- dip_beschluss | title_regex | manual
    yes INTEGER NOT NULL,
    no INTEGER NOT NULL,
    abstain INTEGER NOT NULL,
    invalid INTEGER NOT NULL,
    absent INTEGER NOT NULL,
    xlsx_url TEXT NOT NULL,
    pdf_url TEXT,
    {PROVENANCE},
    agenda_item_id TEXT REFERENCES agenda_item(id)  -- same sitting: Drucksache on the agenda, else the decision's
);

CREATE TABLE IF NOT EXISTS individual_vote (
    id TEXT PRIMARY KEY,                -- "<vote_id>/<row>"
    vote_id TEXT NOT NULL REFERENCES roll_call_vote(id),
    person_id TEXT REFERENCES person(id),
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    fraction TEXT NOT NULL,
    vote TEXT NOT NULL                  -- yes | no | abstain | invalid | absent
);

CREATE TABLE IF NOT EXISTS aw_profile (
    aw_politician_id INTEGER PRIMARY KEY,
    person_id TEXT REFERENCES person(id),
    url TEXT NOT NULL,                  -- the public profile page
    questions INTEGER,                  -- citizen questions on the profile, all periods (statistic_questions)
    questions_answered INTEGER,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS side_job (
    id INTEGER PRIMARY KEY,             -- abgeordnetenwatch sidejob id
    wahlperiode INTEGER NOT NULL,
    person_id TEXT REFERENCES person(id), -- via aw mandate -> politician; NULL if the politician is unmatched
    aw_mandate_id INTEGER NOT NULL,
    label TEXT NOT NULL,                -- the entry as published, e.g. "Mitglied des Beirates, ehrenamtlich (ab …)"
    job_title_extra TEXT,
    category TEXT,                      -- Bundestag category (Verhaltensregeln), label of aw's code
    income_level INTEGER,               -- published Stufe 0..10; NULL: none published
    income_range TEXT,                  -- the Stufe's range, e.g. "1.000 € bis 3.500 €"
    income REAL,                        -- amount as published by aw (`income`); NULL if none
    interval TEXT,                      -- einmalig | monatlich | jährlich
    additional_information TEXT,        -- aw's free text (may contain HTML)
    organization_id INTEGER,            -- aw sidejob_organization id
    organization TEXT,
    city TEXT,
    topics TEXT,                        -- JSON list of aw topic labels
    created TEXT,                       -- date first recorded by aw
    data_change_date TEXT,              -- date aw last changed the record
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS constituency (
    id TEXT PRIMARY KEY,                -- "<election>/<number>", e.g. "btw25/114"
    election TEXT NOT NULL,             -- "btw25"
    number INTEGER NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL,                -- Land abbreviation, as mandate.state
    seat_party TEXT,                    -- party whose candidate got the seat; NULL: no Zweitstimmendeckung
    electorate INTEGER,
    voters INTEGER,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS constituency_result (
    id TEXT PRIMARY KEY,                -- "<election>/<number>/<group order>/<vote>"
    election TEXT NOT NULL,
    constituency_number INTEGER NOT NULL,
    group_kind TEXT NOT NULL,           -- party | individual (Einzelbewerber)
    party TEXT NOT NULL,                -- Gruppenname, e.g. "SPD", "CSU", "GRÜNE"
    vote INTEGER NOT NULL,              -- 1 = Erststimme, 2 = Zweitstimme
    votes INTEGER NOT NULL,
    percent REAL,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS party_fraction (
    party TEXT PRIMARY KEY,             -- as person.party or the Bundeswahlleiterin print it: "CSU", "GRÜNE"
    fraction TEXT NOT NULL              -- the fraction its members sit in: "CDU/CSU", "BÜNDNIS 90/DIE GRÜNEN"
);

CREATE TABLE IF NOT EXISTS constituency_municipality (
    id TEXT PRIMARY KEY,                -- "<election>/<ags>/<constituency number>", e.g. "btw25/02000000/18"
    election TEXT NOT NULL,
    ags TEXT NOT NULL,                  -- Amtlicher Gemeindeschlüssel, 8 digits
    name TEXT NOT NULL,                 -- Gemeindename as in the file, e.g. "Flensburg, Stadt"
    district TEXT NOT NULL,             -- Kreisname, tells the many "Neustadt" apart
    state TEXT NOT NULL,                -- Land abbreviation, as constituency.state
    constituency_number INTEGER NOT NULL,
    split INTEGER NOT NULL,             -- 1: the Gemeinde is split across Wahlkreise, one row per Wahlkreis
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS election_candidacy (
    id TEXT PRIMARY KEY,                -- "<election>/<row in the Gewählte file>"
    election TEXT NOT NULL,
    person_id TEXT REFERENCES person(id),
    last_name TEXT NOT NULL,
    first_names TEXT NOT NULL,
    birth_year INTEGER,
    party TEXT NOT NULL,
    elected_via TEXT NOT NULL,          -- constituency | list
    constituency_number INTEGER,        -- won there, or stood there (list members)
    first_vote_percent REAL,            -- constituency winners only; others: constituency_result
    list_state TEXT,
    list_position INTEGER,
    occupation TEXT,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS mandate_successor (
    id TEXT PRIMARY KEY,                -- "<election>/<Bek.-Nr.>", the list's running number
    election TEXT NOT NULL,
    predecessor_person_id TEXT REFERENCES person(id),
    predecessor_name TEXT NOT NULL,     -- as printed, "Heveling, Ansgar Guido Karl Johannes"
    predecessor_party TEXT NOT NULL,
    predecessor_state TEXT NOT NULL,    -- Land code as in mandate.state ("NW")
    predecessor_seat TEXT NOT NULL,     -- "LL 003" (Landesliste, position) | "WK 044" (Wahlkreis)
    reason TEXT NOT NULL,               -- Ausscheidungsgrund: Ablehnung, Mandatsverzicht, Tod, …
    person_id TEXT REFERENCES person(id),
    name TEXT NOT NULL,                 -- the successor as printed, "Breilmann, Michael"
    birth_year INTEGER NOT NULL,
    party TEXT NOT NULL,
    state TEXT NOT NULL,                -- the Land whose Landesliste the seat comes from
    seat TEXT NOT NULL,                 -- the successor's candidacy, "LL 018" | "WK 282"
    from_date TEXT NOT NULL,            -- Beginn der Mitgliedschaft
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS person_alias (
    alias_id TEXT PRIMARY KEY,          -- a placeholder person id that is gone: a Wikidata QID ("Q109023") made for a
                                        -- government member, or "pdf-<name>" for a speaker read from a PDF
    person_id TEXT NOT NULL REFERENCES person(id),  -- the person it turned out to be
    recorded_at TEXT NOT NULL           -- when the ingest first retired the placeholder, ISO timestamp
);

CREATE TABLE IF NOT EXISTS person_photo (
    person_id TEXT PRIMARY KEY REFERENCES person(id),
    image_url TEXT NOT NULL,            -- the image as downloaded (bundestag.de rendition or Commons thumbnail)
    credit TEXT,                        -- photographer / rights holder as printed by the source, without "©"
    bio_url TEXT,                       -- bundestag.de biography page; NULL for Commons portraits
    local_path TEXT NOT NULL,           -- the unchanged download, relative to data/raw/
    {PROVENANCE}
);

{GOVERNMENT_ROLE}
CREATE TABLE IF NOT EXISTS agenda_item_paragraph (
    id TEXT PRIMARY KEY,                -- "<agenda_item_id>/<position>"
    agenda_item_id TEXT NOT NULL REFERENCES agenda_item(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- chair | comment | procedural | speaker | text
    text TEXT NOT NULL,
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS decision (
    id TEXT PRIMARY KEY,                -- roll-call: roll_call_vote.id ("21/90/7", else "21/90/n<k>");
                                        -- show of hands: "<sitting_id>/h<n>"
    sitting_id TEXT NOT NULL REFERENCES sitting(id),
    agenda_item_id TEXT REFERENCES agenda_item(id),
    n INTEGER NOT NULL,                 -- running number per kind within the sitting
    position INTEGER NOT NULL,          -- order among all decisions of the sitting
    kind TEXT NOT NULL,                 -- namentlich | handzeichen
    subject TEXT NOT NULL,
    drucksache_number TEXT,
    result TEXT,                        -- angenommen | abgelehnt | NULL (not found)
    roll_call_vote_id TEXT REFERENCES roll_call_vote(id),
    text TEXT NOT NULL,                 -- the chair's words the decision was read from
    {PROVENANCE},
    sub_item_id TEXT REFERENCES agenda_sub_item(id),  -- the block item the chair had called up last, else NULL
    vorgang_id TEXT REFERENCES vorgang(id)  -- the only Vorgang of drucksache_number in vorgang_drucksache, else NULL
);

CREATE TABLE IF NOT EXISTS decision_fraction (
    decision_id TEXT NOT NULL REFERENCES decision(id),
    fraction TEXT NOT NULL,             -- normalised, as membership.name
    position TEXT NOT NULL,             -- yes | no | abstain
    PRIMARY KEY (decision_id, fraction)
);

CREATE INDEX IF NOT EXISTS speech_person ON speech(person_id);
CREATE INDEX IF NOT EXISTS agenda_paragraph_item ON agenda_item_paragraph(agenda_item_id);
CREATE INDEX IF NOT EXISTS decision_sitting ON decision(sitting_id);
CREATE INDEX IF NOT EXISTS decision_agenda_item ON decision(agenda_item_id);
CREATE INDEX IF NOT EXISTS agenda_sub_item_item ON agenda_sub_item(agenda_item_id);
CREATE INDEX IF NOT EXISTS vorlage_item ON agenda_item_vorlage(agenda_item_id);
CREATE INDEX IF NOT EXISTS vorlage_drucksache ON agenda_item_vorlage(drucksache_number);
CREATE INDEX IF NOT EXISTS speech_sitting ON speech(sitting_id);
CREATE INDEX IF NOT EXISTS paragraph_speech ON speech_paragraph(speech_id);
CREATE INDEX IF NOT EXISTS vote_person ON individual_vote(person_id);
CREATE INDEX IF NOT EXISTS interjection_speech ON interjection(speech_id);
CREATE INDEX IF NOT EXISTS interjection_person ON interjection(person_id);
CREATE INDEX IF NOT EXISTS vote_vote ON individual_vote(vote_id);
CREATE INDEX IF NOT EXISTS author_person ON drucksache_author(person_id);
CREATE INDEX IF NOT EXISTS author_dip_person ON drucksache_author(dip_person_id);
CREATE INDEX IF NOT EXISTS question_vorgang ON question_activity(vorgang_id);
CREATE INDEX IF NOT EXISTS question_person ON question_activity(person_id);
CREATE INDEX IF NOT EXISTS drucksache_date ON drucksache(date);
CREATE INDEX IF NOT EXISTS person_dip ON person(dip_person_id);
CREATE INDEX IF NOT EXISTS vorgang_position_vorgang ON vorgang_position(vorgang_id);
CREATE INDEX IF NOT EXISTS vorgang_position_date ON vorgang_position(date);
CREATE INDEX IF NOT EXISTS candidacy_person ON election_candidacy(person_id);
CREATE INDEX IF NOT EXISTS municipality_constituency ON constituency_municipality(election, constituency_number);
{GOVERNMENT_ROLE_INDEX};
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after stores already existed: (table, column, declaration)
_ADDED_COLUMNS = [
    ("roll_call_vote", "agenda_item_id", "TEXT REFERENCES agenda_item(id)"),
    ("government_role", "source_kind", "TEXT NOT NULL DEFAULT 'wikidata'"),  # all earlier rows came from Wikidata
    ("vorgang", "verkuendung", "TEXT"),
    ("vorgang", "inkrafttreten", "TEXT"),
    ("speech", "kind", "TEXT NOT NULL DEFAULT 'rede'"),  # all earlier rows are speeches from a <rede>
    ("agenda_item", "no_debate", "INTEGER NOT NULL DEFAULT 0"),  # set on the next protocol ingest
    ("decision", "sub_item_id", "TEXT REFERENCES agenda_sub_item(id)"),  # set on the next decisions ingest
    ("decision", "vorgang_id", "TEXT REFERENCES vorgang(id)"),
    ("speech", "sub_item_id", "TEXT REFERENCES agenda_sub_item(id)"),  # set on the next protocol ingest
    ("agenda_item_vorlage", "via", "TEXT NOT NULL DEFAULT 'title'"),  # rebuilt on the next vorlagen ingest
    ("sitting", "preliminary", "INTEGER NOT NULL DEFAULT 0"),  # the five sitting columns are set on the next
    ("sitting", "final_announced", "TEXT"),  # protocol ingest
    ("sitting", "final_fetched_at", "TEXT"),
    ("sitting", "first_page", "INTEGER"),
    ("sitting", "last_page", "INTEGER"),
    ("person", "fraction", "TEXT"),  # the four derived columns are set by the next ingest (ingest_groups)
    ("speech", "speaker_group", "TEXT"),
    ("speech", "member_fraction", "TEXT"),
    ("drucksache", "originator_groups", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """ALTER TABLE for columns that CREATE TABLE IF NOT EXISTS does not add to an existing store."""
    for table, column, decl in _ADDED_COLUMNS:
        if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    _relax_government_role(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS decision_sub_item ON decision(sub_item_id)")  # column may be new
    conn.execute("CREATE INDEX IF NOT EXISTS speech_sub_item ON speech(sub_item_id)")


def _relax_government_role(conn: sqlite3.Connection) -> None:
    """government_role.wikidata_qid was NOT NULL while Wikidata was the only source. SQLite cannot drop a
    constraint, so the table is rebuilt with its rows (all columns kept by name)."""
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(government_role)")}
    if not cols["wikidata_qid"]["notnull"]:
        return
    names = ", ".join(cols)
    with conn:
        conn.execute("ALTER TABLE government_role RENAME TO government_role_old")
        conn.execute(GOVERNMENT_ROLE)
        conn.execute(f"INSERT INTO government_role ({names}) SELECT {names} FROM government_role_old")
        conn.execute("DROP TABLE government_role_old")  # drops its index too
        conn.execute(GOVERNMENT_ROLE_INDEX)


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[Mapping[str, object]]) -> int:
    """INSERT … ON CONFLICT(primary key) DO UPDATE for every row. Returns the row count."""
    rows = list(rows)
    if not rows:
        return 0
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk = [r["name"] for r in sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])]
    cols = list(rows[0].keys())
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in pk)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT({', '.join(pk)}) DO UPDATE SET {updates}"
        if updates
        else f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    )
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)
