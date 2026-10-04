"""Canned queries. Every row carries source_url and source_document_id."""

import json
import re
import sqlite3
from collections import defaultdict

from bdf.government import KINDS, STALE_AFTER_DAYS, held_on, stale
from bdf.names import DRUCKSACHE_RE, VOTE_VALUES, normalize_name

# DIP activity types that make a person an author of a Drucksache. The others DIP returns per Drucksache are
# roles of their own: Berichterstattung (committee rapporteur on a Beschlussempfehlung) and Antwort
# (government answer, sometimes filed under the asking MdB).
AUTHORSHIP_ACTIVITIES = (
    "Antrag",
    "Kleine Anfrage",
    "Große Anfrage",
    "Entschließungsantrag",
    "Änderungsantrag",
    "Gesetzentwurf",
    "Frage",
)


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
    """Drucksachen the person (co-)authored; rapporteur and answer activities do not count."""
    rows = conn.execute(
        f"""
        SELECT d.number, d.date, d.type, d.title, d.pdf_url, d.originators, d.author_count,
               a.activity_type, a.source_url, a.source_document_id, a.retrieved_at
        FROM drucksache_author a
        JOIN drucksache d ON d.id = a.drucksache_id
        WHERE a.person_id = ? AND d.date BETWEEN ? AND ?
          AND a.activity_type IN ({",".join("?" * len(AUTHORSHIP_ACTIVITIES))})
        ORDER BY d.date, d.number
        """,
        (person_id, start, end, *AUTHORSHIP_ACTIVITIES),
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


def government(conn: sqlite3.Connection, on: str | None = None) -> list[dict]:
    """Government roles (Wikidata, Stammdaten, protocols), by kind and start; ``on`` keeps the roles held that day
    (a protocol row counts as held after its last evidence until contradicted, see government.held_on)."""
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT g.id, g.person_id, g.wikidata_qid, g.name, g.office, g.department, g.kind, g.from_date, g.to_date,
                   g.source_kind, p.is_mdb, g.source_url, g.source_document_id, g.retrieved_at
            FROM government_role g
            LEFT JOIN person p ON p.id = g.person_id
            ORDER BY g.from_date, g.office, g.name
            """
        )
    ]
    if on is not None:
        rows = held_on(rows, on)
    return sorted(rows, key=lambda r: KINDS.index(r["kind"]))


def stale_roles(conn: sqlite3.Connection, days: int = STALE_AFTER_DAYS) -> list[dict]:
    """Protocol-only government roles still treated as current although no protocol has printed them for more than
    ``days`` before the newest sitting (government.stale); oldest evidence first. Empty without sittings."""
    newest = conn.execute("SELECT MAX(date) FROM sitting").fetchone()[0]
    if newest is None:
        return []
    return sorted(stale(government(conn), newest, days), key=lambda r: (r["to_date"], r["name"]))


def photos(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT f.person_id, p.first_name, p.last_name, f.image_url, f.credit, f.bio_url, f.local_path,
               f.source_url, f.source_document_id, f.retrieved_at
        FROM person_photo f JOIN person p ON p.id = f.person_id
        ORDER BY p.last_name, p.first_name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def photos_missing(conn: sqlite3.Connection) -> list[dict]:
    """Sitting members of the latest Wahlperiode without a portrait, with their current fraction."""
    rows = conn.execute(
        """
        SELECT p.id AS person_id, p.first_name, p.last_name,
               (SELECT name FROM membership b WHERE b.person_id = p.id AND b.wahlperiode = m.wahlperiode
                AND b.kind = 'fraction' ORDER BY b.from_date DESC LIMIT 1) AS fraction
        FROM mandate m JOIN person p ON p.id = m.person_id
        WHERE m.wahlperiode = (SELECT MAX(wahlperiode) FROM mandate) AND m.to_date IS NULL
          AND p.id NOT IN (SELECT person_id FROM person_photo)
        ORDER BY p.last_name, p.first_name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def decisions(conn: sqlite3.Connection, sitting_id: str) -> list[dict]:
    """Every decision announced in a sitting, in order, with fraction positions (show of hands) or the
    roll-call totals, the agenda item, and the protocol as source."""
    out = []
    for d in conn.execute(
        """
        SELECT d.*, a.top_id, a.title AS agenda_title, st.date, st.pdf_url,
               v.yes, v.no, v.abstain, v.source_url AS vote_source_url
        FROM decision d
        JOIN sitting st ON st.id = d.sitting_id
        LEFT JOIN agenda_item a ON a.id = d.agenda_item_id
        LEFT JOIN roll_call_vote v ON v.id = d.roll_call_vote_id
        WHERE d.sitting_id = ?
        ORDER BY d.position
        """,
        (sitting_id,),
    ):
        fractions = {
            r["fraction"]: r["position"]
            for r in conn.execute(
                "SELECT fraction, position FROM decision_fraction WHERE decision_id = ? ORDER BY fraction", (d["id"],)
            )
        }
        out.append(
            {
                "decision_id": d["id"],
                "date": d["date"],
                "kind": d["kind"],
                "n": d["n"],
                "top_id": d["top_id"],
                "agenda_title": d["agenda_title"],
                "subject": d["subject"],
                "drucksache_number": d["drucksache_number"],
                "result": d["result"],
                "fractions": fractions,
                "roll_call_vote_id": d["roll_call_vote_id"],
                "roll_call": {k: d[k] for k in ("yes", "no", "abstain")} if d["roll_call_vote_id"] else None,
                "text": d["text"],
                "source_url": d["source_url"],
                "source_document_id": d["source_document_id"],
                "pdf_url": d["pdf_url"],
                "retrieved_at": d["retrieved_at"],
            }
        )
    return out


def _first_page(pages: str | None) -> int | None:
    head = (pages or "").split("-")[0].strip()
    return int(head) if head.isdigit() else None


def _provenance(r: sqlite3.Row) -> dict:
    return {"source_url": r["source_url"], "source_document_id": r["source_document_id"]}


_DIP_ADOPTED = re.compile(r"^(Annahme|Zustimmung)")
_DIP_REJECTED = re.compile(r"^(Ablehnung|Zurückweisung)")
# DIP decisions that are procedure or elections, which `decision` does not hold (docs/design.md)
_DIP_NOT_A_DECISION = re.compile(
    r"Überweis|Überwies|Wahlvorschl|Geschäftsordnung|Tagesordnung|Kenntnisnahme|Zurückverweis|erledigt"
)
_ELECTION_VORGANG = "Besetzung interner Gremien des BT"  # DIP's type for the Bundestag's elections


def decision_check(conn: sqlite3.Connection) -> list[dict]:
    """The decisions parsed from the protocols against DIP's record of them, oldest sitting first:

    - ``result``: a decision whose result disagrees with the beschlusstenor of its DIP step (decision.dip_result).
      A decision on a Beschlussempfehlung is left out: adopting a recommendation to reject is "Ablehnung der
      Vorlage" in DIP.
    - ``missing``: a DIP step in a sitting of the store that records a decision on a Drucksache, with no decision
      of that sitting naming the Drucksache, linked to the step's Vorgang (decision_vorgang) or naming another
      Drucksache of it (a Beschlussempfehlung where DIP names the Antrag). Procedure and elections (DIP's Vorgangstyp
      "Besetzung interner Gremien des BT", a ballot or not) are left out, as in `decision`.

    A check like protocol-gaps, not a correction: the store keeps what the protocol says."""
    out = []
    for d in conn.execute(
        "SELECT d.*, st.date FROM decision d JOIN sitting st ON st.id = d.sitting_id "
        "WHERE d.dip_result IS NOT NULL AND d.result IS NOT NULL AND d.subject NOT LIKE 'Beschlussempfehlung%'"
    ):
        dip = "angenommen" if _DIP_ADOPTED.match(d["dip_result"]) else "abgelehnt" if _DIP_REJECTED.match(
            d["dip_result"]) else None  # fmt: skip
        if dip and dip != d["result"]:
            out.append({"kind": "result", "sitting_id": d["sitting_id"], "date": d["date"], "decision_id": d["id"],
                        "subject": d["subject"], "drucksache_number": d["drucksache_number"], "result": d["result"],
                        "dip_result": d["dip_result"], "dip_position_id": d["dip_position_id"],
                        **_provenance(d)})  # fmt: skip
    sittings = {r["id"]: r for r in conn.execute("SELECT * FROM sitting")}
    of_number: dict[str, set[str]] = defaultdict(set)  # Drucksache number -> its Vorgänge
    for number, vorgang in conn.execute(
        "SELECT d.number, vd.vorgang_id FROM vorgang_drucksache vd JOIN drucksache d ON d.id = vd.drucksache_id"
    ):
        of_number[number].add(vorgang)
    named: dict[str, set[str]] = defaultdict(set)
    linked: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute("SELECT sitting_id, drucksache_number FROM decision WHERE drucksache_number IS NOT NULL"):
        for number in DRUCKSACHE_RE.findall(r[1]):
            named[r[0]].add(number)
            linked[r[0]] |= of_number.get(number, set())
    elections = {r[0] for r in conn.execute("SELECT id FROM vorgang WHERE type = ?", (_ELECTION_VORGANG,))}
    if conn.execute("SELECT name FROM sqlite_master WHERE name = 'decision_vorgang'").fetchone():
        for r in conn.execute(
            "SELECT d.sitting_id, v.vorgang_id FROM decision_vorgang v JOIN decision d ON d.id = v.decision_id"
        ):
            linked[r[0]].add(r[1])
    for p in conn.execute(
        "SELECT vp.*, v.title FROM vorgang_position vp LEFT JOIN vorgang v ON v.id = vp.vorgang_id "
        "WHERE vp.chamber = 'BT' AND vp.document_kind = 'Plenarprotokoll' AND vp.decisions IS NOT NULL"
    ):
        sid = p["document_number"]
        if sid not in sittings or p["vorgang_id"] in elections:
            continue
        for b in json.loads(p["decisions"]):
            if not isinstance(b, dict) or "Wahl" in (b.get("abstimmungsart") or ""):
                continue
            numbers = set(DRUCKSACHE_RE.findall(b.get("dokumentnummer") or ""))
            tenor = b.get("beschlusstenor") or ""
            if (
                not numbers
                or _DIP_NOT_A_DECISION.search(tenor)
                or numbers & named[sid]
                or p["vorgang_id"] in linked[sid]
            ):
                continue
            st = sittings[sid]
            out.append({"kind": "missing", "sitting_id": sid, "date": st["date"], "vorgang_id": p["vorgang_id"],
                        "title": p["title"], "position": p["position"], "dip_result": tenor,
                        "drucksache_number": b.get("dokumentnummer"), "page": b.get("seite"),
                        "dip_position_id": p["id"], **_provenance(p)})  # fmt: skip
    return sorted(out, key=lambda r: (r["date"], r["sitting_id"], r["kind"]))


def protocol_gaps(conn: sqlite3.Connection) -> list[dict]:
    """Per sitting, the Beratungen DIP places in its Plenarprotokoll that no agenda item (or sub-item) of the
    sitting carries, by the rule the cards site uses: a BT Vorgangsposition in a Plenarprotokoll with pages whose
    position names a Beratung, and no item of that sitting with one of the Vorgang's Drucksachen or a Vorlage row
    (agenda_item_vorlage) for the Vorgang. The Vorgang's Drucksachen are those of vorgang_drucksache and those its
    own Vorgangspositionen name, a little wider than the cards' match, so the list can be shorter than theirs.
    An item's Drucksachen include those only a decision under it names (``via = 'decision'``: an
    Entschließungsantrag voted after a Regierungserklärung). DIP's "Geschäftsordnungsantrag …" positions are
    left out: a motion on the agenda, debated under "Zur Geschäftsordnung", not a Beratung of the Vorgang.
    Preliminary sittings are listed even without such a Beratung.

    Each Beratung gets a ``cause``: ``preliminary`` (the sitting's XML is preliminary and no PDF part was read),
    ``after_xml_end`` (it starts after the page the preliminary XML ends on, so the PDF part should have had it)
    or ``in_protocol`` (the protocol has the pages: the item names other Drucksachen, or the parser misses it).
    Oldest sitting first; the source is the protocol."""
    numbers: dict[str, set[str]] = defaultdict(set)  # sitting -> Drucksachen on its items and sub-items
    for sql in (
        "SELECT sitting_id, drucksache_numbers FROM agenda_item",
        "SELECT a.sitting_id, s.drucksache_numbers FROM agenda_sub_item s "
        "JOIN agenda_item a ON a.id = s.agenda_item_id",
        "SELECT a.sitting_id, json_array(v.drucksache_number) AS drucksache_numbers FROM agenda_item_vorlage v "
        "JOIN agenda_item a ON a.id = v.agenda_item_id",
    ):
        for r in conn.execute(sql):
            numbers[r["sitting_id"]].update(json.loads(r["drucksache_numbers"]))
    carried: dict[str, set[str]] = defaultdict(set)  # sitting -> Vorgänge with a Vorlage row there
    for r in conn.execute(
        "SELECT a.sitting_id, v.vorgang_id FROM agenda_item_vorlage v JOIN agenda_item a ON a.id = v.agenda_item_id "
        "WHERE v.vorgang_id IS NOT NULL"
    ):
        carried[r["sitting_id"]].add(r["vorgang_id"])
    drucksachen: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT vd.vorgang_id, d.number FROM vorgang_drucksache vd JOIN drucksache d ON d.id = vd.drucksache_id "
        "UNION SELECT vorgang_id, document_number FROM vorgang_position "
        "WHERE document_kind = 'Drucksache' AND chamber = 'BT' AND document_number IS NOT NULL"
    ):
        drucksachen[r[0]].add(r[1])
    sittings = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM sitting")}
    pdf_speeches = dict(
        conn.execute("SELECT sitting_id, COUNT(*) FROM speech WHERE source_url LIKE '%.pdf' GROUP BY sitting_id")
    )
    unmatched: dict[str, list[dict]] = {}
    for p in conn.execute(
        "SELECT vp.*, v.title FROM vorgang_position vp LEFT JOIN vorgang v ON v.id = vp.vorgang_id "
        "WHERE vp.chamber = 'BT' AND vp.document_kind = 'Plenarprotokoll' AND vp.pages IS NOT NULL "
        "AND vp.position LIKE '%Beratung%' AND vp.position NOT LIKE 'Geschäftsordnungsantrag%' "
        "ORDER BY vp.date, vp.pages"
    ):
        sid, start = p["document_number"], _first_page(p["pages"])
        st = sittings.get(sid)
        if st is None or p["vorgang_id"] in carried[sid] or drucksachen[p["vorgang_id"]] & numbers[sid]:
            continue
        if st["preliminary"] and not st["last_page"]:
            cause = "preliminary"
        elif st["preliminary"] and start is not None and start >= st["last_page"]:
            cause = "after_xml_end"
        else:
            cause = "in_protocol"
        unmatched.setdefault(sid, []).append(
            {"vorgang_id": p["vorgang_id"], "title": p["title"], "position": p["position"], "pages": p["pages"],
             "cause": cause, "vorgang_position_id": p["id"]}
        )  # fmt: skip
    out = []
    for sid in sorted(set(unmatched) | {s for s, st in sittings.items() if st["preliminary"]},
                      key=lambda s: tuple(int(x) for x in s.split("/"))):  # fmt: skip
        st = sittings[sid]
        out.append(
            {
                "sitting_id": sid,
                "date": st["date"],
                "preliminary": bool(st["preliminary"]),
                "final_announced": st["final_announced"],
                "first_page": st["first_page"],
                "last_page": st["last_page"],
                "pdf_speeches": pdf_speeches.get(sid, 0),
                "beratungen": unmatched.get(sid, []),
                "pdf_url": st["pdf_url"],
                "source_url": st["source_url"],
                "source_document_id": st["source_document_id"],
                "retrieved_at": st["retrieved_at"],
            }
        )
    return out
