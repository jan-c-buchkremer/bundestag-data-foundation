"""Parse everything under data/raw and upsert it into SQLite. Never touches the network.

Transactions: one per raw file for protocols and votes, one per ingest_* function otherwise.
"""

import json
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from bdf import (
    fetch_wikidata,
    government,
    parse_biografien,
    parse_comments,
    parse_decisions,
    parse_protocol,
    parse_stammdaten,
    parse_votes,
    parse_wahl,
    parse_wikidata,
    raw,
)
from bdf.config import DIP_BASE_URL, raw_dir
from bdf.db import upsert
from bdf.fetch_aw import aw_dir, mandates_path
from bdf.fetch_bundestag import (
    PROTOCOL_URL,
    biografien_dir,
    photo_path,
    protocols_dir,
    stammdaten_dir,
    vote_path,
    votes_index_path,
)
from bdf.fetch_dip import dip_dir
from bdf.fetch_wahl import ELECTION_OF_WAHLPERIODE, ELECTIONS, gemeinden_csv, gewaehlte_csv, gewaehlte_zip, kerg2_csv
from bdf.match import PersonIndex
from bdf.names import DRUCKSACHE_RE, VOTE_VALUES, normalize_fraction, normalize_name


def ingest_all(conn: sqlite3.Connection) -> None:
    ingest_stammdaten(conn)
    ingest_protocols(conn)
    ingest_votes(conn)
    ingest_dip(conn)
    ingest_decisions(conn)
    ingest_vorlagen(conn)
    ingest_abgeordnetenwatch(conn)
    ingest_side_jobs(conn)
    ingest_wahl(conn)
    ingest_government(conn)
    ingest_photos(conn)


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
    resolvers: dict[int, NameResolver] = {}
    for path in raw.data_files(protocols_dir(), "*/*.xml"):
        protocol = parse_protocol.parse(path)
        prov = raw.read_meta(path).provenance(protocol.document_id)
        sid = protocol.sitting_id
        status = protocol.status
        pdf = path.with_suffix(".pdf")
        # rows read from the final PDF (a preliminary XML's missing end, bdf/protocol_pdf.py) name the PDF
        pdf_prov = raw.read_meta(pdf).provenance(f"{protocol.document_id} (PDF)") if status.last_page else prov
        resolver = resolvers.setdefault(protocol.wahlperiode, NameResolver(PersonIndex(conn, protocol.wahlperiode)))
        for sp in (s.speaker for s in protocol.speeches if s.from_pdf and s.speaker.id.startswith("pdf-")):
            # a PDF speaker no XML names (an MdB's first speech in a new office): the Stammdaten may know them
            sp.id = resolver(f"{sp.first_name} {sp.last_name}") or sp.id
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
                        "preliminary": int(status.preliminary),
                        "final_announced": status.final_announced,
                        "final_fetched_at": None if status.preliminary else prov["retrieved_at"],
                        "first_page": status.first_page,
                        "last_page": status.last_page,
                    }
                ],
            )
            upsert(
                conn,
                "agenda_item",
                [
                    {k: v for k, v in item.items() if k != "from_pdf"}
                    | {"sitting_id": sid}
                    | (pdf_prov if item["from_pdf"] else prov)
                    for item in protocol.agenda_items
                ],
            )
            conn.execute(
                "DELETE FROM agenda_item_paragraph WHERE agenda_item_id IN "
                "(SELECT id FROM agenda_item WHERE sitting_id = ?)",
                (sid,),
            )
            upsert(
                conn,
                "agenda_item_paragraph",
                [
                    {k: v for k, v in p.items() if k not in ("after_speeches", "klasse", "from_pdf")}
                    | (pdf_prov if p.get("from_pdf") else prov)
                    for p in protocol.agenda_paragraphs
                ],
            )
            _replace_sub_items(conn, sid, [{**sub, **prov} for sub in protocol.agenda_sub_items])
            upsert(conn, "person", list(new_persons.values()))
            # a re-ingested protocol replaces its speeches wholesale
            for child in ("interjection", "speech_paragraph"):
                conn.execute(
                    f"DELETE FROM {child} WHERE speech_id IN (SELECT id FROM speech WHERE sitting_id = ?)", (sid,)
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
                        "kind": s.kind,
                        "sub_item_id": s.sub_item_id,
                        **(pdf_prov if s.from_pdf else prov),
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
            upsert(conn, "interjection", interjection_rows(protocol.speeches, resolver))
            _drop_stale_agenda_items(conn, sid, {item["id"] for item in protocol.agenda_items})
        known.update(new_persons)
        from_pdf = [s for s in protocol.speeches if s.from_pdf]
        unmatched = sorted({s.speaker.printed for s in from_pdf if s.speaker.id.startswith("pdf-")})
        print(
            f"protocol {sid} ({protocol.date}): {len(protocol.agenda_items)} agenda items, "
            f"{len(protocol.speeches)} speeches, {len(new_persons)} new non-MdB speakers"
            + (f"; PRELIMINARY (final announced for {status.final_announced or '?'})" if status.preliminary else "")
            + (f", XML ends on page {status.last_page}, then {sum(i['from_pdf'] for i in protocol.agenda_items)} "
               f"agenda items and {len(from_pdf)} speeches from the PDF" if status.last_page else "")
            + (f"; no PDF part ({'PDF not fetched' if not pdf.exists() else 'XML end not found in the PDF'})"
               if status.preliminary and not status.last_page else "")
            + (f"; speakers not in any XML: {', '.join(unmatched)}" if unmatched else "")
        )  # fmt: skip


class NameResolver:
    """MdB id for a printed name ("Dr. Götz Frömming", "Beatrix von Storch"): the surname is the last one,
    two or three words; cached, since the same few hundred names recur in every protocol."""

    def __init__(self, index: PersonIndex):
        self.index = index
        self.cache: dict[str, str | None] = {}

    def __call__(self, name: str) -> str | None:
        if name not in self.cache:
            words = name.split()
            self.cache[name] = next(
                (pid for k in (1, 2, 3) if len(words) > k
                 if (pid := self.index.match(" ".join(words[-k:]), " ".join(words[:-k]))) is not None),
                None,
            )  # fmt: skip
        return self.cache[name]


def interjection_rows(speeches: list, resolve: NameResolver) -> list[dict]:
    """One row per actor of every part of every comment paragraph (docs/design.md, interjection)."""
    rows = []
    for s in speeches:
        for n, (kind, text) in enumerate(s.paragraphs, start=1):
            if kind != "comment":
                continue
            for i, part in enumerate(parse_comments.parse(text), start=1):
                for j, a in enumerate(part.actors, start=1):
                    rows.append(
                        {
                            "id": f"{s.id}/{n}/{i}/{j}", "speech_id": s.id, "paragraph": n, "part": i,
                            "kind": part.kind, "actor": a.kind, "fraction": a.fraction,
                            "person_id": resolve(a.name) if a.kind == "person" and a.name else None,
                            "name": a.name, "text": part.text,
                            "to_person_id": resolve(part.to.name) if part.to and part.to.name else None,
                            "to_name": part.to.name if part.to else None,
                        }
                    )  # fmt: skip
    return rows


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
    _ingest_vorgang_positions(conn)
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


def _latest_list_records(directory: Path) -> list[tuple[dict, raw.RawMeta]]:
    """Records of all DIP list files in ``directory``, each id once, from the most recently retrieved file.

    `update` re-reads 14 days back, so consecutive date-range files overlap and the same record
    appears in several of them.
    """
    latest: dict[str, tuple[dict, raw.RawMeta]] = {}
    for path in raw.data_files(directory, "*.json"):
        meta = raw.read_meta(path)
        for record in raw.read_json(path):
            seen = latest.get(record["id"])
            if seen is None or meta.retrieved_at >= seen[1].retrieved_at:
                latest[record["id"]] = (record, meta)
    return list(latest.values())


def _ingest_drucksachen(conn: sqlite3.Connection) -> None:
    count_mismatches: list[str] = []
    rows: dict[str, list[dict]] = defaultdict(list)
    for d, list_meta in _latest_list_records(dip_dir() / "drucksache"):
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
        authors = _author_rows(d["id"], doc_id)
        # autoren_anzahl is 0 on Schriftliche Fragen that list over a hundred askers; where the
        # activities were fetched, the number of distinct persons in them wins
        if authors is not None and len(authors) != (d.get("autoren_anzahl") or 0):
            count_mismatches.append(f"{d['dokumentnummer']} ({d.get('autoren_anzahl')} → {len(authors)})")
            rows["drucksache"][-1]["author_count"] = len(authors)
        rows["drucksache_author"] += authors or []
        vorgaenge, links = _vorgang_rows(d["id"])
        rows["vorgang"] += vorgaenge
        rows["vorgang_drucksache"] += links
    with conn:
        for table in ("drucksache", "vorgang", "drucksache_author", "vorgang_drucksache"):
            upsert(conn, table, rows[table])
        conn.execute(
            "UPDATE drucksache_author SET person_id = "
            "(SELECT id FROM person WHERE person.dip_person_id = drucksache_author.dip_person_id)"
        )
    print(
        f"dip: {len(rows['drucksache'])} drucksachen, {len(rows['drucksache_author'])} author activities, "
        f"{len(rows['vorgang_drucksache'])} vorgang links"
    )
    if count_mismatches:
        print(
            f"dip: {len(count_mismatches)} drucksachen where autoren_anzahl disagrees with the activities "
            f"(activities used, first 10): " + ", ".join(count_mismatches[:10])
        )


def _author_rows(drucksache_id: str, doc_id: str) -> list[dict] | None:
    """None if the activities of this Drucksache were not fetched (yet)."""
    path = dip_dir() / "aktivitaet" / f"drucksache-{drucksache_id}.json"
    if not path.exists():
        return None
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
            "verkuendung": _json_or_none(v.get("verkuendung") or []),
            "inkrafttreten": _json_or_none(v.get("inkrafttreten") or []),
            **meta.provenance(f"DIP Vorgang {v['id']}", url=f"{DIP_BASE_URL}/vorgang/{v['id']}"),
        }
        for v in vorgaenge
    ]
    links = [{"vorgang_id": v["id"], "drucksache_id": drucksache_id} for v in vorgaenge]
    return rows, links


def _vorgangsposition_records() -> list[tuple[dict, raw.RawMeta]]:
    """BT positions plus BR/BV/EK ones from the separate directory `fetch_dip` fills for those
    (`vorgangsposition_other`); ids are unique across zuordnung values, so both merge without collision."""
    return _latest_list_records(dip_dir() / "vorgangsposition") + _latest_list_records(
        dip_dir() / "vorgangsposition_other"
    )


def _ingest_vorgang_positions(conn: sqlite3.Connection) -> None:
    """Every step of a Vorgang from the date-range lists: Drucksache, Beratung, Durchgang, Zustimmung,
    Vermittlungsausschuss, … across the Bundestag, Bundesrat, Bundesversammlung and Europakammer."""
    rows = [_vorgang_position_row(p, meta) for p, meta in _vorgangsposition_records()]
    with conn:
        upsert(conn, "vorgang_position", rows)
    print(f"dip: {len(rows)} vorgangspositionen")


def _vorgang_position_row(p: dict, meta: raw.RawMeta) -> dict:
    f = p.get("fundstelle") or {}
    pages = None
    if f.get("anfangsseite"):
        pages = str(f["anfangsseite"])
        if f.get("endseite") and f["endseite"] != f["anfangsseite"]:
            pages += f"-{f['endseite']}"
    return {
        "id": p["id"],
        "vorgang_id": p["vorgang_id"],
        "date": p["datum"],
        "position": p["vorgangsposition"],
        "chamber": p.get("zuordnung"),
        "document_kind": f.get("dokumentart") or p.get("dokumentart"),
        "document_number": f.get("dokumentnummer"),
        "document_type": f.get("drucksachetyp"),
        "pdf_url": f.get("pdf_url"),
        "pages": pages,
        "originators": json.dumps([u.get("titel") for u in p.get("urheber", [])], ensure_ascii=False),
        "ressort": _json_or_none(
            [{"titel": r.get("titel"), "federfuehrend": bool(r.get("federfuehrend"))} for r in p.get("ressort") or []]
        ),
        "decisions": _json_or_none(p.get("beschlussfassung") or []),
        **meta.provenance(f"DIP Vorgangsposition {p['id']}", url=f"{DIP_BASE_URL}/vorgangsposition/{p['id']}"),
    }


def _json_or_none(items: list) -> str | None:
    return json.dumps(items, ensure_ascii=False) if items else None


def _link_votes_to_dip(conn: sqlite3.Connection) -> None:
    """Pair each sitting day's roll-call votes with DIP's 'Namentliche Abstimmung' decisions.

    Same count on the day: pair by order (protocol page vs. Abstimmnr). Otherwise fall back
    to a Drucksache number in the vote title, else leave the vote unlinked.
    """
    decisions_by_date: dict[str, list[tuple[tuple[int, str], str | None, str | None]]] = defaultdict(list)
    for pos, _ in _latest_list_records(dip_dir() / "vorgangsposition"):
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


# --- decisions -----------------------------------------------------------------------------


def _drop_stale_agenda_items(conn: sqlite3.Connection, sitting_id: str, keep: set[str]) -> None:
    """Drop the agenda items of a sitting that a re-parse no longer finds: the final XML can number the items read
    from a preliminary protocol's PDF differently. Decisions and votes pointing at them are re-linked by their own
    ingest; speeches, paragraphs and sub-items of the sitting are replaced before this runs."""
    stale = [
        r["id"] for r in conn.execute("SELECT id FROM agenda_item WHERE sitting_id = ?", (sitting_id,))
        if r["id"] not in keep
    ]  # fmt: skip
    for item_id in stale:
        conn.execute("UPDATE decision SET agenda_item_id = NULL WHERE agenda_item_id = ?", (item_id,))
        conn.execute("UPDATE roll_call_vote SET agenda_item_id = NULL WHERE agenda_item_id = ?", (item_id,))
        conn.execute("DELETE FROM agenda_item_vorlage WHERE agenda_item_id = ?", (item_id,))
        conn.execute("DELETE FROM agenda_item WHERE id = ?", (item_id,))


def _replace_sub_items(conn: sqlite3.Connection, sitting_id: str, rows: list[dict]) -> None:
    """Upsert a sitting's sub-items and drop the ones a re-parse no longer finds (with what points at them)."""
    upsert(conn, "agenda_sub_item", rows)
    stale = [
        r["id"]
        for r in conn.execute(
            "SELECT s.id FROM agenda_sub_item s JOIN agenda_item a ON a.id = s.agenda_item_id WHERE a.sitting_id = ?",
            (sitting_id,),
        )
        if r["id"] not in {row["id"] for row in rows}
    ]
    for sub_id in stale:
        conn.execute("UPDATE decision SET sub_item_id = NULL WHERE sub_item_id = ?", (sub_id,))
        conn.execute("UPDATE speech SET sub_item_id = NULL WHERE sub_item_id = ?", (sub_id,))
        conn.execute("DELETE FROM agenda_item_vorlage WHERE sub_item_id = ?", (sub_id,))
        conn.execute("DELETE FROM agenda_sub_item WHERE id = ?", (sub_id,))


def _vorgang_by_drucksache(conn: sqlite3.Connection) -> dict[str, str]:
    """Drucksache number -> Vorgang id, for the Drucksachen that belong to exactly one Vorgang."""
    vorgaenge: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT d.number, vd.vorgang_id FROM vorgang_drucksache vd JOIN drucksache d ON d.id = vd.drucksache_id"
    ):
        vorgaenge[r["number"]].add(r["vorgang_id"])
    return {number: next(iter(ids)) for number, ids in vorgaenge.items() if len(ids) == 1}


def ingest_vorlagen(conn: sqlite3.Connection) -> None:
    """agenda_item_vorlage (one row per Drucksache of an agenda item; of a sub-item where the item has
    sub-items, plus the Drucksachen of the item outside them) and decision.vorgang_id. Both are rebuilt from
    the store, after agenda items, decisions and DIP are in: the Vorgang comes from vorgang_drucksache."""
    vorgang = _vorgang_by_drucksache(conn)
    subs: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for sub in conn.execute("SELECT * FROM agenda_sub_item ORDER BY agenda_item_id, position"):
        subs[sub["agenda_item_id"]].append(sub)
    rows = []
    for item in conn.execute("SELECT * FROM agenda_item ORDER BY sitting_id, position"):
        prov = {k: item[k] for k in ("source_url", "source_document_id", "retrieved_at")}
        in_subs = set()
        for sub in subs.get(item["id"], []):
            for number in json.loads(sub["drucksache_numbers"]):
                in_subs.add(number)
                rows.append(_vorlage_row(sub["id"], item["id"], sub["id"], number, vorgang, prov))
        for number in json.loads(item["drucksache_numbers"]):
            if number not in in_subs:
                rows.append(_vorlage_row(item["id"], item["id"], None, number, vorgang, prov))
    # a Drucksache decided under an item without being in its title: mostly the Entschließungsanträge to a bill,
    # debated with it and voted on after it, which the item's title does not list
    seen = {r["id"] for r in rows}
    on_item = {(r["agenda_item_id"], r["drucksache_number"]) for r in rows}
    for d in conn.execute(
        "SELECT * FROM decision WHERE agenda_item_id IS NOT NULL AND drucksache_number IS NOT NULL ORDER BY position"
    ):
        prov = {k: d[k] for k in ("source_url", "source_document_id", "retrieved_at")}
        for number in DRUCKSACHE_RE.findall(d["drucksache_number"]):
            row = _vorlage_row(d["sub_item_id"] or d["agenda_item_id"], d["agenda_item_id"], d["sub_item_id"], number,
                               vorgang, prov, via="decision")  # fmt: skip
            if row["id"] not in seen and (d["agenda_item_id"], number) not in on_item:
                seen.add(row["id"])
                rows.append(row)
    with conn:
        conn.execute("DELETE FROM agenda_item_vorlage")
        upsert(conn, "agenda_item_vorlage", rows)
        conn.executemany(
            "UPDATE decision SET vorgang_id = ? WHERE id = ?",
            [
                (vorgang.get(r["drucksache_number"]), r["id"])
                for r in conn.execute("SELECT id, drucksache_number FROM decision")
            ],
        )
    linked = sum(1 for r in rows if r["vorgang_id"])
    decided = sum(1 for r in rows if r["via"] == "decision")
    print(f"vorlagen: {len(rows)} Drucksachen on agenda items ({linked} with a Vorgang), {decided} of them only "
          "named in a decision under the item")  # fmt: skip


def _vorlage_row(
    owner: str, item_id: str, sub_id: str | None, number: str, vorgang: dict, prov: dict, via: str = "title"
) -> dict:
    return {
        "id": f"{owner}/{number}", "agenda_item_id": item_id, "sub_item_id": sub_id,
        "drucksache_number": number, "vorgang_id": vorgang.get(number), **prov, "via": via,
    }  # fmt: skip


def ingest_decisions(conn: sqlite3.Connection) -> None:
    """Decisions announced by the chair (bdf/parse_decisions.py), linked to roll_call_vote, and
    roll_call_vote.agenda_item_id. Runs after votes and DIP: linking uses the votes' counts and Drucksachen.

    Re-parses the protocols: the decision stream needs the document order of agenda item paragraphs
    and speech paragraphs, which the store does not keep."""
    votes: dict[str, list[dict]] = defaultdict(list)
    for v in conn.execute("SELECT id, sitting_id, number, yes, no, drucksache_number FROM roll_call_vote"):
        votes[v["sitting_id"]].append(dict(v))
    rows: dict[str, list[dict]] = defaultdict(list)
    vorgang = _vorgang_by_drucksache(conn)
    known_subs = {r["id"] for r in conn.execute("SELECT id FROM agenda_sub_item")}
    sittings: list[str] = []
    pending: list = []
    previous = None
    counts = defaultdict(int)
    for path in raw.data_files(protocols_dir(), "*/*.xml"):
        protocol = parse_protocol.parse(path)
        sid = protocol.sitting_id
        consecutive = previous == (protocol.wahlperiode, protocol.number - 1)
        decisions = parse_decisions.extract(protocol, pending=pending if consecutive else [])
        parse_decisions.link(decisions, votes.get(sid, []))
        final = {v["id"]: (v["yes"], v["no"]) for v in votes.get(sid, [])}
        pending = [
            (d, final.get(d.roll_call_vote_id)) for d in decisions if d.kind == "namentlich" and d.counts is None
        ]
        previous = (protocol.wahlperiode, protocol.number)
        prov = raw.read_meta(path).provenance(protocol.document_id)
        sittings.append(sid)
        for d in decisions:
            counts[d.kind] += 1
            rows["decision"].append(
                {
                    "id": d.id, "sitting_id": sid, "agenda_item_id": d.agenda_item_id, "n": d.n,
                    "position": d.position, "kind": d.kind, "subject": d.subject,
                    "drucksache_number": d.drucksache_number, "result": d.result,
                    "roll_call_vote_id": d.roll_call_vote_id, "text": d.text, **prov,
                    "sub_item_id": d.sub_item_id if d.sub_item_id in known_subs else None,
                    "vorgang_id": vorgang.get(d.drucksache_number),
                }
            )  # fmt: skip
            rows["decision_fraction"] += [
                {"decision_id": d.id, "fraction": f, "position": pos} for f, pos in d.fractions.items()
            ]
    # a roll-call result read out in the next sitting updates a decision of the previous one after
    # it was built; rows are written only once every protocol has been read
    with conn:
        for sid in sittings:
            conn.execute(
                "DELETE FROM decision_fraction WHERE decision_id IN (SELECT id FROM decision WHERE sitting_id = ?)",
                (sid,),
            )
            conn.execute("DELETE FROM decision WHERE sitting_id = ?", (sid,))
        upsert(conn, "decision", rows["decision"])
        upsert(conn, "decision_fraction", rows["decision_fraction"])
        _link_votes_to_agenda(conn)
    linked = sum(1 for r in rows["decision"] if r["roll_call_vote_id"])
    print(
        f"decisions: {counts['handzeichen']} by show of hands, {counts['namentlich']} roll-call "
        f"({linked} linked to a roll_call_vote)"
    )


def _link_votes_to_agenda(conn: sqlite3.Connection) -> None:
    """roll_call_vote.agenda_item_id: the agenda item of the same sitting listing one of the vote's
    Drucksachen, else the agenda item of the vote's decision row."""
    items = defaultdict(list)
    for a in conn.execute("SELECT id, sitting_id, drucksache_numbers FROM agenda_item ORDER BY sitting_id, position"):
        items[a["sitting_id"]].append((a["id"], set(json.loads(a["drucksache_numbers"]))))
    from_decision = dict(
        conn.execute("SELECT roll_call_vote_id, agenda_item_id FROM decision WHERE roll_call_vote_id IS NOT NULL")
    )
    updates = []
    for v in conn.execute("SELECT id, sitting_id, drucksache_number FROM roll_call_vote"):
        numbers = set(DRUCKSACHE_RE.findall(v["drucksache_number"] or ""))
        item = next((a for a, ns in items.get(v["sitting_id"], []) if numbers & ns), None)
        updates.append((item or from_decision.get(v["id"]), v["id"]))
    conn.executemany("UPDATE roll_call_vote SET agenda_item_id = ? WHERE id = ?", updates)


# --- abgeordnetenwatch --------------------------------------------------------------------


def ingest_abgeordnetenwatch(conn: sqlite3.Connection) -> None:
    for path in raw.data_files(aw_dir(), "wp*-politicians.json"):
        wp = int(path.stem.split("-")[0][2:])
        index = PersonIndex(conn, wp)
        meta = raw.read_meta(path)
        matched, unmatched, disagree, profiles = [], [], [], []
        for p in raw.read_json(path):
            pid = index.match(p["last_name"], p["first_name"], str(p.get("year_of_birth") or ""))
            profiles.append(
                {
                    "aw_politician_id": p["id"],
                    "person_id": pid,
                    "url": p["abgeordnetenwatch_url"],
                    "questions": p.get("statistic_questions"),
                    "questions_answered": p.get("statistic_questions_answered"),
                    **meta.provenance(f"aw politician {p['id']}", url=p["api_url"]),
                }
            )
            if pid is None:
                unmatched.append(p["label"])
                continue
            ext = p.get("ext_id_bundestagsverwaltung")
            if ext and ext != pid:
                disagree.append(f"{p['label']}: aw says {ext}, name match says {pid}")
            matched.append((p["id"], p.get("qid_wikidata"), pid))
        with conn:
            conn.executemany("UPDATE person SET aw_politician_id = ?, wikidata_qid = ? WHERE id = ?", matched)
            upsert(conn, "aw_profile", profiles)
        print(
            f"abgeordnetenwatch WP {wp}: {len(matched)} matched, {len(unmatched)} unmatched, "
            f"{len(disagree)} where ext_id_bundestagsverwaltung disagrees"
        )
        for u in unmatched:
            print("    unmatched:", u)


# aw codes for the Bundestag's categories and published levels (https://www.abgeordnetenwatch.de/api/entitaeten/sidejob)
SIDE_JOB_CATEGORY = {
    "29231": "Beteiligung an Kapital- oder Personengesellschaften",
    "29647": "Entgeltliche Tätigkeiten neben dem Mandat",
    "29229": "Funktionen in Körperschaften und Anstalten des öffentlichen Rechts",
    "29228": "Funktionen in Unternehmen",
    "29230": "Funktionen in Vereinen, Verbänden und Stiftungen",
    "29232": "Spenden/Zuwendungen für politische Tätigkeit",
    "29233": "Vereinbarungen über künftige Tätigkeiten oder Vermögensvorteile",
    "29234": "Berufliche Tätigkeit vor der Mitgliedschaft im Deutschen Bundestag",
}
INCOME_RANGE = {
    0: "1 € bis 1.000 €",
    1: "1.000 € bis 3.500 €",
    2: "3.500 € bis 7.000 €",
    3: "7.000 € bis 15.000 €",
    4: "15.000 € bis 30.000 €",
    5: "30.000 € bis 50.000 €",
    6: "50.000 € bis 75.000 €",
    7: "75.000 € bis 100.000 €",
    8: "100.000 € bis 150.000 €",
    9: "150.000 € bis 250.000 €",
    10: "ab 250.000 €",
}
INTERVAL = {"0": "einmalig", "1": "monatlich", "2": "jährlich"}


def ingest_side_jobs(conn: sqlite3.Connection) -> None:
    """Replaces the side jobs of each Wahlperiode, so entries abgeordnetenwatch has withdrawn disappear."""
    for path in raw.data_files(aw_dir(), "wp*-sidejobs.json"):
        wp = int(path.stem.split("-")[0][2:])
        meta = raw.read_meta(path)
        politician = {m["id"]: m["politician"]["id"] for m in raw.read_json(mandates_path(wp))}
        person = dict(conn.execute("SELECT aw_politician_id, person_id FROM aw_profile WHERE person_id IS NOT NULL"))
        rows = []
        for j in raw.read_json(path):
            mandate = j["mandates"][0]["id"]
            level = int(j["income_level"]) if j.get("income_level") not in (None, "") else None
            org = j.get("sidejob_organization") or {}
            rows.append(
                {
                    "id": j["id"],
                    "wahlperiode": wp,
                    "person_id": person.get(politician.get(mandate)),
                    "aw_mandate_id": mandate,
                    "label": j["label"],
                    "job_title_extra": j.get("job_title_extra"),
                    "category": SIDE_JOB_CATEGORY.get(j.get("category") or "", j.get("category")),
                    "income_level": level,
                    "income_range": INCOME_RANGE.get(level),
                    "income": j.get("income"),
                    "interval": INTERVAL.get(j.get("interval") or ""),
                    "additional_information": j.get("additional_information"),
                    "organization_id": org.get("id"),
                    "organization": org.get("label"),
                    "city": (j.get("field_city") or {}).get("label"),
                    "topics": json.dumps([t["label"] for t in j.get("field_topics") or []], ensure_ascii=False),
                    "created": datetime.fromtimestamp(j["created"], UTC).date().isoformat()
                    if j.get("created")
                    else None,
                    "data_change_date": j.get("data_change_date"),
                    **meta.provenance(f"aw sidejob {j['id']}", url=j["api_url"]),
                }
            )
        with conn:
            conn.execute("DELETE FROM side_job WHERE wahlperiode = ?", (wp,))
            upsert(conn, "side_job", rows)
        unmatched = sum(r["person_id"] is None for r in rows)
        print(f"abgeordnetenwatch side jobs WP {wp}: {len(rows)}, {unmatched} without a matched person")


# --- Bundeswahlleiterin ------------------------------------------------------------------


def ingest_wahl(conn: sqlite3.Connection) -> None:
    """Official election results; elected candidates are matched to MdB ids of the Wahlperiode they formed."""
    for wp, election in ELECTION_OF_WAHLPERIODE.items():
        if election not in ELECTIONS:
            continue
        csv_path, kerg2 = gewaehlte_csv(election), kerg2_csv(election)
        if not csv_path.exists() or not kerg2.exists():
            print(f"wahl {election}: nothing fetched")
            continue
        constituencies, results, as_of = parse_wahl.parse_kerg2(kerg2)
        prov = raw.read_meta(kerg2).provenance(parse_wahl.document_id("Ergebnisse nach Wahlkreisen", election, as_of))
        elected, as_of = parse_wahl.parse_gewaehlte(csv_path)
        cprov = raw.read_meta(gewaehlte_zip(election)).provenance(parse_wahl.document_id("Gewählte", election, as_of))
        index = PersonIndex(conn, wp)
        members = conn.execute(
            "SELECT p.id, p.last_name, substr(p.birth_date, 1, 4) AS year FROM person p "
            "JOIN mandate m ON m.person_id = p.id WHERE m.wahlperiode = ?",
            (wp,),
        ).fetchall()
        candidacies, unmatched = [], []
        for n, c in enumerate(elected, start=1):
            pid = _match_elected(index, members, c)
            if pid is None:
                unmatched.append(f"{c['first_names']} {c['last_name']} ({c['party']}, {c['birth_year']})")
            keep = ("last_name", "first_names", "birth_year", "party", "elected_via", "constituency_number",
                    "first_vote_percent", "list_state", "list_position", "occupation")  # fmt: skip
            candidacies.append(
                {"id": f"{election}/{n}", "election": election, "person_id": pid, **{k: c[k] for k in keep}, **cprov}
            )
        with conn:
            upsert(
                conn,
                "constituency",
                [{"id": f"{election}/{c['number']}", "election": election, **c, **prov} for c in constituencies],
            )
            upsert(
                conn,
                "constituency_result",
                [
                    {"id": f"{election}/{r['constituency_number']}/{r['group_order']}/{r['vote']}",
                     "election": election, **{k: v for k, v in r.items() if k != "group_order"}, **prov}
                    for r in results
                ],
            )  # fmt: skip
            upsert(conn, "election_candidacy", candidacies)
        seatless = sum(c["seat_party"] is None for c in constituencies)
        print(
            f"wahl {election}: {len(constituencies)} Wahlkreise ({seatless} without a seat), {len(results)} results, "
            f"{len(candidacies)} elected, {len(unmatched)} unmatched"
        )
        for u in unmatched:
            print("    unmatched:", u)
        ingest_municipalities(conn, election)


def ingest_municipalities(conn: sqlite3.Connection, election: str) -> None:
    """The Wahlkreiseinteilung: which Wahlkreis(e) each Gemeinde belongs to."""
    path = gemeinden_csv(election)
    if not path.exists():
        print(f"wahl {election}: no Wahlkreiseinteilung fetched")
        return
    rows, as_of = parse_wahl.parse_gemeinden(path)
    prov = raw.read_meta(path).provenance(parse_wahl.document_id("Wahlkreiseinteilung", election, as_of))
    with conn:
        conn.execute("DELETE FROM constituency_municipality WHERE election = ?", (election,))
        upsert(
            conn,
            "constituency_municipality",
            [{"id": f"{election}/{r['ags']}/{r['constituency_number']}", "election": election, **r, **prov}
             for r in rows],
        )  # fmt: skip
    split = len({r["ags"] for r in rows if r["split"]})
    print(f"wahl {election}: {len(rows)} Gemeinde rows ({split} Gemeinden split across Wahlkreise)")


def _match_elected(index: PersonIndex, members: list[sqlite3.Row], c: dict) -> str | None:
    """The official file has every given name and the legal surname, the Stammdaten the name in use:
    "Joachim-Friedrich Martin Josef Merz" is Friedrich Merz, "Saleh, Kassem Taher" is Kassem Taher Saleh.
    Try the full names, then each given name, then a unique member with that surname part and birth year."""
    year = str(c["birth_year"] or "")
    for first in [c["first_names"], *re.split(r"[\s-]+", c["first_names"])[1:]]:
        pid = index.match(c["last_name"], first, year)
        if pid is not None:
            return pid
    last = set(normalize_name(c["last_name"]).split())
    same = [m["id"] for m in members if m["year"] == year and last & set(normalize_name(m["last_name"]).split())]
    return same[0] if len(same) == 1 else None


# --- Government roster: Wikidata + Stammdaten + protocols ---------------------------------

# role texts that look like a federal government office; what government.parse_role leaves of them is reported
_GOVERNMENT_ROLE_RE = re.compile(
    r"^(Bundeskanzler|Bundesminister|Parl\. Staatssekretär|Parlamentarische"
    r"|Staatsminister(in)? (beim|bei der|für) |Staatssekretär(in)? (im|in der|beim|bei der) )"
)
_COMMISSIONER_RE = re.compile(r"^Beauftragte[r]? der Bundesregierung")


def ingest_government(conn: sqlite3.Connection) -> None:
    """government_role, replaced wholesale: Wikidata roles, Stammdaten government memberships and the government
    roles printed in the protocols, merged by bdf.government (one row per person + kind + department, dates from
    the best source, open single-holder roles closed by a later holder in the protocols)."""
    evidence, matched, new_persons = _wikidata_evidence(conn)
    stammdaten = _stammdaten_evidence(conn)
    protocol, unparsed = _protocol_evidence(conn)
    rows, inferences = government.merge(evidence + stammdaten + protocol)
    with conn:
        conn.execute("DELETE FROM government_role")
        upsert(conn, "person", list(new_persons.values()))
        upsert(conn, "government_role", rows)
        conn.executemany(
            "UPDATE person SET wikidata_qid = ? WHERE id = ? AND wikidata_qid IS NULL",
            [(qid, pid) for qid, (pid, how) in matched.items() if how == "name"],
        )
        # person rows made for a roster member who has since been matched (or dropped from the roster)
        orphans = "SELECT id FROM person WHERE id LIKE 'Q%' AND id NOT IN (SELECT person_id FROM government_role)"
        conn.execute(f"DELETE FROM person_photo WHERE person_id IN ({orphans})")
        conn.execute(f"DELETE FROM person WHERE id IN ({orphans})")
    hows = [how for _, how in matched.values()]
    print(
        f"government: evidence from wikidata {len(evidence)}, stammdaten {len(stammdaten)}, protocol {len(protocol)}; "
        f"Wikidata holders: {len(matched)} persons, {hows.count('qid')} matched by QID, {hows.count('name')} by "
        f"name, {hows.count('new')} new"
    )
    _report_government(conn, rows, inferences, unparsed)


def _wikidata_evidence(
    conn: sqlite3.Connection,
) -> tuple[list[government.Evidence], dict[str, tuple[str | None, str]], dict[str, dict]]:
    """Wikidata roles as evidence. Holders are matched to persons by Wikidata QID, then by name + birth date
    (non-MdB speakers from the protocols have no birth date and match on the name alone); the rest get a person
    row with id = QID and is_mdb = 0. Returns the evidence, {qid: (person_id, how)} and the new person rows."""
    path = fetch_wikidata.government_path()
    if not path.exists():
        print("government: Wikidata roster not fetched")
        return [], {}, {}
    meta = raw.read_meta(path)
    roles = parse_wikidata.parse_roles(raw.read_json(path))
    persons = conn.execute(
        "SELECT id, first_name, last_name, birth_date, wikidata_qid FROM person WHERE id NOT LIKE 'Q%'"
    ).fetchall()
    by_qid = {p["wikidata_qid"]: p["id"] for p in persons if p["wikidata_qid"]}
    by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for p in persons:
        by_name[normalize_name(f"{p['first_name']} {p['last_name']}")].append(p)
    index = PersonIndex(conn, _current_wahlperiode(conn))

    def match(role: parse_wikidata.Role) -> tuple[str | None, str]:
        if role.qid in by_qid:
            return by_qid[role.qid], "qid"
        same = [p for p in by_name.get(normalize_name(role.name), []) if p["birth_date"] in (None, role.birth_date)]
        if len(same) == 1:
            return same[0]["id"], "name"
        first, last = parse_wikidata.split_name(role.name, role.family_name)
        pid = index.match(last, first, (role.birth_date or "")[:4] or None)
        birth = next((p["birth_date"] for p in persons if p["id"] == pid), None)
        if pid is not None and not pid.startswith("Q") and birth in (None, role.birth_date):
            return pid, "name"
        return None, "new"

    matched: dict[str, tuple[str | None, str]] = {r.qid: match(r) for r in roles}
    new_persons = {}
    for r in roles:
        if matched[r.qid][1] == "new" and r.qid not in new_persons:
            first, last = parse_wikidata.split_name(r.name, r.family_name)
            new_persons[r.qid] = {
                "id": r.qid, "first_name": first, "last_name": last, "birth_date": r.birth_date,
                "party": r.party, "is_mdb": 0, "role": r.office, "wikidata_qid": r.qid,
                **meta.provenance(f"Wikidata {r.qid}", url=f"https://www.wikidata.org/wiki/{r.qid}"),
            }  # fmt: skip
    evidence = []
    for r in roles:
        parsed = government.parse_role(r.office)
        evidence.append(
            government.Evidence(
                source_kind="wikidata", person_id=matched[r.qid][0] or r.qid, name=r.name, kind=r.kind,
                department=r.department or (parsed.department if parsed else None), from_date=r.from_date,
                to_date=r.to_date, wikidata_qid=r.qid, office=r.office, id=r.id,
                provenance=meta.provenance(f"Wikidata {r.id}", url=f"https://www.wikidata.org/wiki/{r.qid}#P39"),
            )
        )  # fmt: skip
    return evidence, matched, new_persons


def _stammdaten_evidence(conn: sqlite3.Connection) -> list[government.Evidence]:
    """Memberships of kind other whose function is a government office: FKT_LANG "Parlamentarischer
    Staatssekretär" at INS_LANG "Bundesministerium der Finanzen", from the start of the current government."""
    evidence = []
    for m in conn.execute(
        "SELECT m.person_id, m.role, m.name, m.from_date, m.to_date, m.source_url, m.source_document_id, "
        "m.retrieved_at, p.first_name, p.last_name, p.wikidata_qid FROM membership m "
        "JOIN person p ON p.id = m.person_id WHERE m.kind = 'other' AND m.from_date >= ? ORDER BY m.id",
        (fetch_wikidata.GOVERNMENT_START,),
    ):
        parsed = government.parse_role(m["role"] or "")
        if parsed is None or (parsed.department is not None and parsed.kind != "kanzler"):
            continue  # not an office, or a function that names a department itself (none in the Stammdaten)
        evidence.append(
            government.Evidence(
                source_kind="stammdaten", person_id=m["person_id"], name=f"{m['first_name']} {m['last_name']}",
                kind=parsed.kind, department=parsed.department or m["name"], from_date=m["from_date"],
                to_date=m["to_date"], wikidata_qid=m["wikidata_qid"],
                provenance={k: m[k] for k in ("source_url", "source_document_id", "retrieved_at")},
            )
        )  # fmt: skip
    return evidence


def _protocol_evidence(conn: sqlite3.Connection) -> tuple[list[government.Evidence], dict[str, int]]:
    """One piece of evidence per person + office printed as a speaker's role (speech.speaker_role, else the non-MdB
    person's role) since the start of the current government: first and last sitting date, provenance of the
    first. Also returns the government-looking role texts that could not be parsed, with their speech counts."""
    found: dict[tuple, government.Evidence] = {}
    unparsed: dict[str, int] = defaultdict(int)
    for s in conn.execute(
        "SELECT s.person_id, COALESCE(s.speaker_role, CASE WHEN p.is_mdb = 0 THEN p.role END) AS role, st.date, "
        "s.source_url, s.source_document_id, s.retrieved_at, p.first_name, p.last_name, p.wikidata_qid "
        "FROM speech s JOIN sitting st ON st.id = s.sitting_id JOIN person p ON p.id = s.person_id "
        "WHERE st.date >= ? ORDER BY st.date, s.sitting_id, s.position",
        (fetch_wikidata.GOVERNMENT_START,),
    ):
        role = s["role"]
        parsed = government.parse_role(role) if role else None
        if parsed is None or parsed.department is None:
            if role and _GOVERNMENT_ROLE_RE.match(role):
                unparsed[role] += 1
            continue
        e = government.Evidence(
            source_kind="protocol", person_id=s["person_id"], name=f"{s['first_name']} {s['last_name']}",
            kind=parsed.kind, department=parsed.department, from_date=s["date"], to_date=s["date"],
            wikidata_qid=s["wikidata_qid"],
            provenance={k: s[k] for k in ("source_url", "source_document_id", "retrieved_at")},
        )  # fmt: skip
        if e.key in found:
            found[e.key].to_date = s["date"]
        else:
            found[e.key] = e
    return list(found.values()), dict(unparsed)


def _current_wahlperiode(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(wahlperiode), 21) FROM mandate").fetchone()[0]


def _report_government(
    conn: sqlite3.Connection, rows: list[dict], inferences: list[government.Inference], unparsed: dict[str, int]
) -> None:
    """Counts per kind and source, the inferences, the cabinet at the latest sitting, and the government speakers
    in the protocols who still have no role in the roster."""
    counts = {k: {s: 0 for s in government.SOURCES} for k in government.KINDS}
    for r in rows:
        counts[r["kind"]][r["source_kind"]] += 1
    print(f"government: {len(rows)} roles")
    for kind, by_source in counts.items():
        print(f"    {kind:<15} {sum(by_source.values()):>3}  " + ", ".join(f"{s} {n}" for s, n in by_source.items()))
    for i in inferences:
        print(f"government: inferred end: {i}")
    latest = conn.execute("SELECT MAX(date) FROM sitting").fetchone()[0]
    if latest:
        cabinet = [r for r in government.held_on(rows, latest) if r["kind"] in government.SINGLE_HOLDER]
        print(f"government: cabinet at the latest sitting ({latest}): {len(cabinet)} (Kanzler and Bundesminister)")
        for r in cabinet:
            print(f"    {r['office']}: {r['name']} ({r['source_kind']})")
    for role, n in sorted(unparsed.items()):
        print(f"government: role text not parsed ({n} speeches): {role}")
    in_roster = {r["person_id"] for r in rows}
    missing, commissioners = {}, {}
    for s in conn.execute(
        "SELECT s.person_id, s.speaker_role, MAX(st.date) AS last FROM speech s "
        "JOIN sitting st ON st.id = s.sitting_id WHERE st.date >= ? AND s.speaker_role IS NOT NULL "
        "GROUP BY s.person_id, s.speaker_role ORDER BY s.speaker_role",
        (fetch_wikidata.GOVERNMENT_START,),
    ):
        if s["person_id"] in in_roster:
            continue
        if _GOVERNMENT_ROLE_RE.match(s["speaker_role"]):
            missing[(s["person_id"], s["speaker_role"])] = s["last"]
        elif _COMMISSIONER_RE.match(s["speaker_role"]):
            commissioners[(s["person_id"], s["speaker_role"])] = s["last"]
    print(f"government: {len(missing)} government speakers in the protocols have no role in the roster")
    for label, found in (("", missing), (" (Beauftragte, not government members; not in the roster)", commissioners)):
        for (pid, role), last in found.items():
            name = conn.execute("SELECT first_name, last_name FROM person WHERE id = ?", (pid,)).fetchone()
            print(f"    {name['first_name']} {name['last_name']} ({pid}): {role}, last {last}{label}")


# --- bundestag.de biographies + Commons: portraits ----------------------------------------


def ingest_photos(conn: sqlite3.Connection) -> None:
    """One portrait per person, replaced wholesale: the bundestag.de biography card (matched by name, checked against
    the fraction), else, for government members, the Commons image of their Wikidata item."""
    rows: dict[str, dict] = {}
    pages = raw.data_files(biografien_dir(), "page-*.html")
    if pages:
        rows.update(_bundestag_photos(conn, pages))
    else:
        print("photos: bundestag.de biographies not fetched")
    rows.update({pid: row for pid, row in _commons_photos(conn).items() if pid not in rows})
    with conn:
        conn.execute("DELETE FROM person_photo")
        upsert(conn, "person_photo", list(rows.values()))


def _bundestag_photos(conn: sqlite3.Connection, pages: list[Path]) -> dict[str, dict]:
    wp = _current_wahlperiode(conn)
    index = PersonIndex(conn, wp)
    fractions = {
        r["person_id"]: r["name"]
        for r in conn.execute(
            "SELECT person_id, name FROM membership WHERE wahlperiode = ? AND kind = 'fraction' ORDER BY from_date",
            (wp,),
        )
    }  # the latest fraction wins
    members = conn.execute(
        "SELECT p.id, p.first_name, p.last_name FROM person p JOIN mandate m ON m.person_id = p.id "
        "WHERE m.wahlperiode = ?",
        (wp,),
    ).fetchall()
    rows: dict[str, dict] = {}
    cards = 0
    unmatched, other_fraction, missing_file, twice = [], [], [], []
    for page in pages:
        meta = raw.read_meta(page)
        for card in parse_biografien.parse(page.read_text(encoding="utf-8")):
            cards += 1
            pid = index.match(card.last_name, card.first_name) or _by_fraction(members, fractions, card)
            if pid is None:
                unmatched.append(f"{card.printed_name} ({card.fraction})")
                continue
            if card.fraction and fractions.get(pid) and normalize_fraction(fractions[pid]) != card.fraction:
                other_fraction.append(
                    f"{card.printed_name}: {card.fraction} on bundestag.de, {fractions[pid]} in store"
                )
            local = photo_path(card)
            if not card.image_url or not local.exists():
                missing_file.append(card.printed_name)
                continue
            if pid in rows:
                twice.append(f"{card.printed_name} ({pid})")
            rows[pid] = {
                "person_id": pid,
                "image_url": card.image_url,
                "credit": card.credit,
                "bio_url": card.bio_url,
                "local_path": local.relative_to(raw_dir()).as_posix(),
                **meta.provenance(f"bundestag.de Biografie {card.printed_name}"),
            }
    sitting = {
        r["person_id"]
        for r in conn.execute("SELECT person_id FROM mandate WHERE wahlperiode = ? AND to_date IS NULL", (wp,))
    }
    print(
        f"photos: {cards} bundestag.de cards, {len(rows)} matched ({len(sitting & rows.keys())}/{len(sitting)} "
        f"sitting WP {wp} members), {len(unmatched)} unmatched, {len(missing_file)} without a downloaded image"
    )
    for label, items in (("unmatched", unmatched), ("fraction differs", other_fraction), ("two cards", twice),
                         ("image not downloaded", missing_file)):  # fmt: skip
        for item in items:
            print(f"    {label}: {item}")
    return rows


def _by_fraction(members: list[sqlite3.Row], fractions: dict[str, str], card: parse_biografien.Card) -> str | None:
    """A unique WP member with the card's surname, first given name and fraction (namesakes in the store)."""
    last = normalize_name(card.last_name)
    first = normalize_name(card.first_name).split(" ")[0]
    same = [
        m["id"]
        for m in members
        if normalize_name(m["last_name"]) == last
        and normalize_name(m["first_name"]).split(" ")[0] == first
        and normalize_fraction(fractions.get(m["id"])) == card.fraction
    ]
    return same[0] if len(same) == 1 else None


def _commons_photos(conn: sqlite3.Connection) -> dict[str, dict]:
    government, commons = fetch_wikidata.government_path(), fetch_wikidata.commons_path()
    if not government.exists() or not commons.exists():
        return {}
    images = parse_wikidata.parse_commons(raw.read_json(commons))
    meta = raw.read_meta(commons)
    person_of = {r["id"]: r["person_id"] for r in conn.execute("SELECT id, person_id FROM government_role")}
    rows = {}
    for role in parse_wikidata.parse_roles(raw.read_json(government)):
        image = images.get(role.image or "")
        pid = person_of.get(role.id)
        local = fetch_wikidata.photo_path(image.title) if image else None
        if pid is None or image is None or local is None or not local.exists():
            continue
        rows[pid] = {
            "person_id": pid,
            "image_url": image.thumb_url,
            "credit": image.credit,
            "bio_url": None,
            "local_path": local.relative_to(raw_dir()).as_posix(),
            **meta.provenance(f"Wikimedia Commons {image.title}", url=image.page_url),
        }
    return rows
