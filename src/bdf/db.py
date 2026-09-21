"""SQLite schema and a generic upsert. See docs/design.md for the model."""

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path

PROVENANCE = "source_url TEXT NOT NULL, source_document_id TEXT NOT NULL, retrieved_at TEXT NOT NULL"

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
    {PROVENANCE}
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
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS agenda_item (
    id TEXT PRIMARY KEY,                -- "<sitting_id>/<position>"
    sitting_id TEXT NOT NULL REFERENCES sitting(id),
    position INTEGER NOT NULL,
    top_id TEXT NOT NULL,               -- XML top-id attribute, e.g. "Tagesordnungspunkt 3"
    title TEXT,
    drucksache_numbers TEXT NOT NULL,   -- JSON array of "21/7300"
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS speech (
    id TEXT PRIMARY KEY,                -- XML rede/@id, "-2", "-3" … when split
    sitting_id TEXT NOT NULL REFERENCES sitting(id),
    agenda_item_id TEXT REFERENCES agenda_item(id),
    position INTEGER NOT NULL,          -- order within the sitting
    person_id TEXT NOT NULL REFERENCES person(id),
    speaker_name TEXT NOT NULL,
    speaker_role TEXT,
    fraction TEXT,
    text TEXT NOT NULL,                 -- clean text: paragraphs of kind 'text', blank-line joined
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS speech_paragraph (
    id TEXT PRIMARY KEY,                -- "<speech_id>/<position>"
    speech_id TEXT NOT NULL REFERENCES speech(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- text | comment | chair | procedural
    text TEXT NOT NULL
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
    {PROVENANCE}
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

CREATE TABLE IF NOT EXISTS vorgang (
    id TEXT PRIMARY KEY,                -- DIP id
    wahlperiode INTEGER NOT NULL,
    type TEXT,
    title TEXT NOT NULL,
    status TEXT,                        -- beratungsstand
    subjects TEXT NOT NULL,             -- JSON array (sachgebiet)
    initiators TEXT NOT NULL,           -- JSON array (initiative)
    {PROVENANCE}
);

CREATE TABLE IF NOT EXISTS vorgang_drucksache (
    vorgang_id TEXT NOT NULL REFERENCES vorgang(id),
    drucksache_id TEXT NOT NULL REFERENCES drucksache(id),
    PRIMARY KEY (vorgang_id, drucksache_id)
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
    {PROVENANCE}
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

CREATE INDEX IF NOT EXISTS speech_person ON speech(person_id);
CREATE INDEX IF NOT EXISTS speech_sitting ON speech(sitting_id);
CREATE INDEX IF NOT EXISTS paragraph_speech ON speech_paragraph(speech_id);
CREATE INDEX IF NOT EXISTS vote_person ON individual_vote(person_id);
CREATE INDEX IF NOT EXISTS vote_vote ON individual_vote(vote_id);
CREATE INDEX IF NOT EXISTS author_person ON drucksache_author(person_id);
CREATE INDEX IF NOT EXISTS author_dip_person ON drucksache_author(dip_person_id);
CREATE INDEX IF NOT EXISTS drucksache_date ON drucksache(date);
CREATE INDEX IF NOT EXISTS person_dip ON person(dip_person_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


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
