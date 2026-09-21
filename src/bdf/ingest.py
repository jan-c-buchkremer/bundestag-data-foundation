"""Parse everything under data/raw and upsert it into SQLite. Never touches the network.

Transactions: one per raw file for protocols and votes, one per ingest_* function otherwise.
"""

import json
import re
import sqlite3
from collections import defaultdict

from bdf import parse_protocol, parse_stammdaten, parse_votes, raw
from bdf.config import DIP_BASE_URL
from bdf.db import upsert
from bdf.fetch_aw import aw_dir
from bdf.fetch_bundestag import PROTOCOL_URL, protocols_dir, stammdaten_dir, vote_path, votes_index_path
from bdf.fetch_dip import dip_dir
from bdf.match import PersonIndex
from bdf.names import DRUCKSACHE_RE, VOTE_VALUES


def ingest_all(conn: sqlite3.Connection) -> None:
    ingest_stammdaten(conn)
    ingest_protocols(conn)
    ingest_votes(conn)
    ingest_dip(conn)
    ingest_abgeordnetenwatch(conn)


# --- bundestag.de -------------------------------------------------------------------------


def ingest_stammdaten(conn: sqlite3.Connection) -> None:
    xml_path = stammdaten_dir() / "MDB_STAMMDATEN.XML"
    if not xml_path.exists():
        print("stammdaten: nothing fetched")
        return
    tables = parse_stammdaten.parse(xml_path, raw.read_meta(stammdaten_dir() / "MdB-Stammdaten.zip"))
    with conn:
        # parser rows carry no dip/aw/wikidata ids, so the upsert leaves existing links untouched
        for table, rows in tables.items():
            upsert(conn, table, rows)
    n = {t: len(rows) for t, rows in tables.items()}
    print(f"stammdaten: {n['person']} persons, {n['mandate']} mandates, {n['membership']} memberships")


def ingest_protocols(conn: sqlite3.Connection) -> None:
    known = {r["id"] for r in conn.execute("SELECT id FROM person")}
    for path in raw.data_files(protocols_dir(), "*/*.xml"):
        protocol = parse_protocol.parse(path)
        prov = raw.read_meta(path).provenance(protocol.document_id)
        sid = protocol.sitting_id
        new_persons = {}
        for sp in (s.speaker for s in protocol.speeches):
            if sp.id not in known and sp.id not in new_persons:
                new_persons[sp.id] = {
                    "id": sp.id,
                    "first_name": sp.first_name,
                    "last_name": sp.last_name,
                    "name_prefix": sp.name_prefix,
                    "academic_title": sp.academic_title,
                    "is_mdb": 0,
                    "role": sp.role,
                    **prov,
                }
        with conn:
            upsert(
                conn,
                "sitting",
                [
                    {
                        "id": sid,
                        "wahlperiode": protocol.wahlperiode,
                        "number": protocol.number,
                        "date": protocol.date,
                        "start_time": protocol.start_time,
                        "end_time": protocol.end_time,
                        "xml_url": prov["source_url"],
                        "pdf_url": PROTOCOL_URL.format(wp=protocol.wahlperiode, nr=protocol.number, ext="pdf"),
                        **prov,
                    }
                ],
            )
            upsert(conn, "agenda_item", [{**item, "sitting_id": sid, **prov} for item in protocol.agenda_items])
            upsert(conn, "person", list(new_persons.values()))
            # a re-ingested protocol replaces its speeches wholesale
            conn.execute(
                "DELETE FROM speech_paragraph WHERE speech_id IN (SELECT id FROM speech WHERE sitting_id = ?)", (sid,)
            )
            conn.execute("DELETE FROM speech WHERE sitting_id = ?", (sid,))
            upsert(
                conn,
                "speech",
                [
                    {
                        "id": s.id,
                        "sitting_id": sid,
                        "agenda_item_id": s.agenda_item_id,
                        "position": s.position,
                        "person_id": s.speaker.id,
                        "speaker_name": s.speaker.printed,
                        "speaker_role": s.speaker.role,
                        "fraction": s.speaker.fraction,
                        "text": s.text,
                        **prov,
                    }
                    for s in protocol.speeches
                ],
            )
            upsert(
                conn,
                "speech_paragraph",
                [
                    {"id": f"{s.id}/{n}", "speech_id": s.id, "position": n, "kind": kind, "text": text}
                    for s in protocol.speeches
                    for n, (kind, text) in enumerate(s.paragraphs, start=1)
                ],
            )
        known.update(new_persons)
        print(
            f"protocol {sid} ({protocol.date}): {len(protocol.agenda_items)} agenda items, "
            f"{len(protocol.speeches)} speeches, {len(new_persons)} new non-MdB speakers"
        )


def ingest_votes(conn: sqlite3.Connection) -> None:
    if not votes_index_path().exists():
        print("votes: nothing fetched")
        return
    indexes: dict[int, PersonIndex] = {}
    unmatched: list[str] = []
    for entry in raw.read_json(votes_index_path()):
        xlsx = vote_path(entry["xlsx_url"])
        if not xlsx.exists():
            continue
        rc = parse_votes.parse(xlsx)
        index = indexes.setdefault(rc.wahlperiode, PersonIndex(conn, rc.wahlperiode))
        sitting_id = f"{rc.wahlperiode}/{rc.sitting}"
        rows = []
        for n, r in enumerate(rc.rows, start=1):
            pid = index.match(r.last_name, r.first_name)
            if pid is None:
                unmatched.append(f"{rc.id}: {r.first_name} {r.last_name} ({r.fraction})")
            rows.append(
                {
                    "id": f"{rc.id}/{n}",
                    "vote_id": rc.id,
                    "person_id": pid,
                    "last_name": r.last_name,
                    "first_name": r.first_name,
                    "fraction": r.fraction,
                    "vote": r.vote,
                }
            )
        totals = {v: sum(1 for r in rc.rows if r.vote == v) for v in VOTE_VALUES}
        with conn:
            has_sitting = conn.execute("SELECT 1 FROM sitting WHERE id = ?", (sitting_id,)).fetchone()
            upsert(
                conn,
                "roll_call_vote",
                [
                    {
                        "id": rc.id,
                        "sitting_id": sitting_id if has_sitting else None,
                        "number": rc.number,
                        "date": entry["date"],
                        "title": entry["title"],
                        "drucksache_number": None,
                        "vorgang_id": None,
                        "link_method": None,
                        **totals,
                        "xlsx_url": entry["xlsx_url"],
                        "pdf_url": entry.get("pdf_url"),
                        **raw.read_meta(xlsx).provenance(f"NA {rc.id}"),
                    }
                ],
            )
            upsert(conn, "individual_vote", rows)
        print(f"vote {rc.id} ({entry['date']}): {len(rows)} votes, {totals['yes']} yes / {totals['no']} no")
    if unmatched:
        print(f"votes: {len(unmatched)} rows without person match (first 20):")
        for u in unmatched[:20]:
            print("   ", u)


# --- DIP ----------------------------------------------------------------------------------


def ingest_dip(conn: sqlite3.Connection) -> None:
    if not dip_dir().exists():
        print("dip: nothing fetched")
        return
    _ingest_dip_persons(conn)
    _ingest_drucksachen(conn)
    _link_votes_to_dip(conn)


def _ingest_dip_persons(conn: sqlite3.Connection) -> None:
    for path in raw.data_files(dip_dir() / "person", "wp*.json"):
        wp = int(path.stem[2:])
        index = PersonIndex(conn, wp)
        persons = raw.read_json(path)
        matches = [(p["id"], pid) for p in persons if (pid := index.match(p.get("nachname", ""), p.get("vorname", "")))]
        with conn:
            conn.executemany("UPDATE person SET dip_person_id = ? WHERE id = ?", matches)
        rest = len(persons) - len(matches)
        print(f"dip persons WP {wp}: {len(matches)} matched to MdB ids, {rest} not (non-MdB or ambiguous)")


def _ingest_drucksachen(conn: sqlite3.Connection) -> None:
    counts = {"drucksache": 0, "drucksache_author": 0, "vorgang_drucksache": 0}
    for path in raw.data_files(dip_dir() / "drucksache", "*.json"):
        list_meta = raw.read_meta(path)
        rows: dict[str, list[dict]] = defaultdict(list)
        for d in raw.read_json(path):
            doc_id = f"BT-Drs. {d['dokumentnummer']}"
            rows["drucksache"].append(
                {
                    "id": d["id"],
                    "number": d["dokumentnummer"],
                    "wahlperiode": d["wahlperiode"],
                    "type": d.get("drucksachetyp"),
                    "title": d["titel"],
                    "date": d["datum"],
                    "pdf_url": (d.get("fundstelle") or {}).get("pdf_url"),
                    "publisher": d.get("herausgeber"),
                    "originators": json.dumps([u.get("titel") for u in d.get("urheber", [])], ensure_ascii=False),
                    "author_count": d.get("autoren_anzahl"),
                    **list_meta.provenance(doc_id, url=f"{DIP_BASE_URL}/drucksache/{d['id']}"),
                }
            )
            rows["drucksache_author"] += _author_rows(d["id"], doc_id)
            vorgaenge, links = _vorgang_rows(d["id"])
            rows["vorgang"] += vorgaenge
            rows["vorgang_drucksache"] += links
        with conn:
            for table in ("drucksache", "vorgang", "drucksache_author", "vorgang_drucksache"):
                upsert(conn, table, rows[table])
        for table in counts:
            counts[table] += len(rows[table])
    with conn:
        conn.execute(
            "UPDATE drucksache_author SET person_id = "
            "(SELECT id FROM person WHERE person.dip_person_id = drucksache_author.dip_person_id)"
        )
    print(
        f"dip: {counts['drucksache']} drucksachen, {counts['drucksache_author']} author activities, "
        f"{counts['vorgang_drucksache']} vorgang links"
    )


def _author_rows(drucksache_id: str, doc_id: str) -> list[dict]:
    path = dip_dir() / "aktivitaet" / f"drucksache-{drucksache_id}.json"
    if not path.exists():
        return []
    meta = raw.read_meta(path)
    rows = {
        a["person_id"]: {
            "id": f"{drucksache_id}/{a['person_id']}",
            "drucksache_id": drucksache_id,
            "dip_person_id": a["person_id"],
            "person_id": None,
            "name": a["titel"],
            "activity_type": a.get("aktivitaetsart"),
            **meta.provenance(doc_id, url=f"{DIP_BASE_URL}/aktivitaet/{a['id']}"),
        }
        for a in raw.read_json(path)
        if a.get("person_id")
    }
    return list(rows.values())


def _vorgang_rows(drucksache_id: str) -> tuple[list[dict], list[dict]]:
    path = dip_dir() / "vorgang" / f"drucksache-{drucksache_id}.json"
    if not path.exists():
        return [], []
    meta = raw.read_meta(path)
    vorgaenge = raw.read_json(path)
    rows = [
        {
            "id": v["id"],
            "wahlperiode": v["wahlperiode"],
            "type": v.get("vorgangstyp"),
            "title": v["titel"],
            "status": v.get("beratungsstand"),
            "subjects": json.dumps(v.get("sachgebiet", []), ensure_ascii=False),
            "initiators": json.dumps(v.get("initiative", []), ensure_ascii=False),
            **meta.provenance(f"DIP Vorgang {v['id']}", url=f"{DIP_BASE_URL}/vorgang/{v['id']}"),
        }
        for v in vorgaenge
    ]
    links = [{"vorgang_id": v["id"], "drucksache_id": drucksache_id} for v in vorgaenge]
    return rows, links


def _link_votes_to_dip(conn: sqlite3.Connection) -> None:
    """Pair each sitting day's roll-call votes with DIP's 'Namentliche Abstimmung' decisions.

    Same count on the day: pair by order (protocol page vs. Abstimmnr). Otherwise fall back
    to a Drucksache number in the vote title, else leave the vote unlinked.
    """
    decisions_by_date: dict[str, list[tuple[tuple[int, str], str | None, str | None]]] = defaultdict(list)
    for path in raw.data_files(dip_dir() / "vorgangsposition", "*.json"):
        for pos in raw.read_json(path):
            for b in pos.get("beschlussfassung") or []:
                if b.get("abstimmungsart") != "Namentliche Abstimmung":
                    continue
                page = re.match(r"(\d+)([A-D]?)", b.get("seite") or "0")
                key = (int(page.group(1)), page.group(2))
                decisions_by_date[pos["datum"]].append((key, b.get("dokumentnummer"), pos.get("vorgang_id")))
    votes_by_date: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for v in conn.execute("SELECT id, date, number, title FROM roll_call_vote ORDER BY date, number"):
        votes_by_date[v["date"]].append(v)
    known_vorgaenge = {r["id"] for r in conn.execute("SELECT id FROM vorgang")}
    updates: list[tuple] = []
    for date, day_votes in votes_by_date.items():
        decisions = sorted(decisions_by_date.get(date, []))
        if len(decisions) == len(day_votes):
            for v, (_, number, vorgang_id) in zip(day_votes, decisions, strict=True):
                updates.append(
                    (number, vorgang_id if vorgang_id in known_vorgaenge else None, "dip_beschluss", v["id"])
                )
            continue
        for v in day_votes:
            m = DRUCKSACHE_RE.search(v["title"] or "")
            if m:
                updates.append((m.group(1), None, "title_regex", v["id"]))
    with conn:
        conn.executemany(
            "UPDATE roll_call_vote SET drucksache_number = ?, vorgang_id = ?, link_method = ? WHERE id = ?", updates
        )
    total = sum(len(v) for v in votes_by_date.values())
    print(f"dip: {len(updates)}/{total} roll-call votes linked to a Drucksache")


# --- abgeordnetenwatch --------------------------------------------------------------------


def ingest_abgeordnetenwatch(conn: sqlite3.Connection) -> None:
    for path in raw.data_files(aw_dir(), "wp*-politicians.json"):
        wp = int(path.stem.split("-")[0][2:])
        index = PersonIndex(conn, wp)
        matched, unmatched, disagree = [], [], []
        for p in raw.read_json(path):
            pid = index.match(p["last_name"], p["first_name"], str(p.get("year_of_birth") or ""))
            if pid is None:
                unmatched.append(p["label"])
                continue
            ext = p.get("ext_id_bundestagsverwaltung")
            if ext and ext != pid:
                disagree.append(f"{p['label']}: aw says {ext}, name match says {pid}")
            matched.append((p["id"], p.get("qid_wikidata"), pid))
        with conn:
            conn.executemany("UPDATE person SET aw_politician_id = ?, wikidata_qid = ? WHERE id = ?", matched)
        print(
            f"abgeordnetenwatch WP {wp}: {len(matched)} matched, {len(unmatched)} unmatched, "
            f"{len(disagree)} where ext_id_bundestagsverwaltung disagrees"
        )
        for u in unmatched:
            print("    unmatched:", u)
