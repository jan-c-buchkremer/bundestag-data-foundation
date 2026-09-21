"""Canned queries. Every row carries source_url and source_document_id."""

import sqlite3

from bdf.names import VOTE_VALUES, normalize_name


def resolve_person(conn: sqlite3.Connection, who: str) -> sqlite3.Row:
    """Accept an MdB id ('11004006') or a name ('Bärbel Bas', 'Bas')."""
    if who.isdigit():
        row = conn.execute("SELECT * FROM person WHERE id = ?", (who,)).fetchone()
        if row is None:
            raise SystemExit(f"no person with id {who}")
        return row
    wanted = normalize_name(who)
    hits = [
        r
        for r in conn.execute("SELECT * FROM person")
        if wanted in (normalize_name(f"{r['first_name']} {r['last_name']}"), normalize_name(r["last_name"]))
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"no person matches {who!r}")
    raise SystemExit(
        f"{who!r} is ambiguous: " + ", ".join(f"{r['first_name']} {r['last_name']} ({r['id']})" for r in hits)
    )


def speeches(conn: sqlite3.Connection, person_id: str, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.id AS speech_id, st.date, st.id AS sitting_id, a.top_id, a.title AS agenda_title,
               s.speaker_name, s.speaker_role, s.fraction, s.text,
               s.source_url, s.source_document_id, s.retrieved_at
        FROM speech s
        JOIN sitting st ON st.id = s.sitting_id
        LEFT JOIN agenda_item a ON a.id = s.agenda_item_id
        WHERE s.person_id = ? AND st.date BETWEEN ? AND ?
        ORDER BY st.date, s.position
        """,
        (person_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def votes(conn: sqlite3.Connection, person_id: str, start: str, end: str) -> list[dict]:
    """Each roll-call vote in the range with the person's vote and their fraction's majority."""
    out = []
    for v in conn.execute(
        "SELECT * FROM roll_call_vote WHERE date BETWEEN ? AND ? ORDER BY date, number",
        (start, end),
    ):
        own = conn.execute(
            "SELECT vote, fraction FROM individual_vote WHERE vote_id = ? AND person_id = ?",
            (v["id"], person_id),
        ).fetchone()
        fraction_line = None
        if own:
            counts = conn.execute(
                "SELECT vote, COUNT(*) AS n FROM individual_vote WHERE vote_id = ? AND fraction = ? "
                "GROUP BY vote ORDER BY n DESC",
                (v["id"], own["fraction"]),
            ).fetchall()
            fraction_line = {
                "fraction": own["fraction"],
                "majority": counts[0]["vote"],
                "counts": {c["vote"]: c["n"] for c in counts},
            }
        out.append(
            {
                "vote_id": v["id"],
                "date": v["date"],
                "title": v["title"],
                "drucksache_number": v["drucksache_number"],
                "vorgang_id": v["vorgang_id"],
                "link_method": v["link_method"],
                "result": {k: v[k] for k in VOTE_VALUES},
                "own_vote": own["vote"] if own else None,
                "fraction_line": fraction_line,
                "source_url": v["source_url"],
                "source_document_id": v["source_document_id"],
                "pdf_url": v["pdf_url"],
                "retrieved_at": v["retrieved_at"],
            }
        )
    return out


def drucksachen(conn: sqlite3.Connection, person_id: str, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT d.number, d.date, d.type, d.title, d.pdf_url, d.originators, d.author_count,
               a.activity_type, a.source_url, a.source_document_id, a.retrieved_at
        FROM drucksache_author a
        JOIN drucksache d ON d.id = a.drucksache_id
        WHERE a.person_id = ? AND d.date BETWEEN ? AND ?
        ORDER BY d.date, d.number
        """,
        (person_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def corpus(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """One clean speech per row: the input for the topic landscape."""
    rows = conn.execute(
        """
        SELECT s.id AS speech_id, s.person_id, p.first_name, p.last_name, p.is_mdb, s.speaker_role,
               s.fraction, p.party, st.date, st.id AS sitting_id, a.top_id, a.title AS agenda_title,
               s.text, s.source_url, s.source_document_id
        FROM speech s
        JOIN sitting st ON st.id = s.sitting_id
        JOIN person p ON p.id = s.person_id
        LEFT JOIN agenda_item a ON a.id = s.agenda_item_id
        WHERE st.date BETWEEN ? AND ? AND length(s.text) > 0
        ORDER BY st.date, s.position
        """,
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]
