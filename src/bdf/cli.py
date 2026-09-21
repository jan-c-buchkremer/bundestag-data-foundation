"""bdf: fetch raw sources, ingest them into SQLite, run canned queries."""

import argparse
import json
import sys
from datetime import date

from bdf import db, fetch_aw, fetch_bundestag, fetch_dip, ingest, queries, raw
from bdf.config import db_path


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _add_range(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="start", type=_date, required=True, metavar="DATE")
    p.add_argument("--to", dest="end", type=_date, required=True, metavar="DATE")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bdf", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="download raw sources to data/raw")
    fs = f.add_subparsers(dest="source", required=True)
    fs.add_parser("stammdaten", help="MdB master data (whole file)")
    fp = fs.add_parser("protocols", help="Plenarprotokoll XML by sitting number")
    fp.add_argument("--wp", type=int, default=21)
    fp.add_argument("--from", dest="first", type=int, required=True, metavar="NR")
    fp.add_argument("--to", dest="last", type=int, required=True, metavar="NR")
    fv = fs.add_parser("votes", help="roll-call vote XLSX/PDF by date range")
    _add_range(fv)
    fd = fs.add_parser("dip", help="DIP Drucksachen, authors, Vorgänge, Vorgangspositionen, persons by date range")
    fd.add_argument("--wp", type=int, default=21)
    _add_range(fd)
    fa = fs.add_parser("aw", help="abgeordnetenwatch mandates and politicians of a Wahlperiode")
    fa.add_argument("--wp", type=int, default=21, choices=sorted(fetch_aw.PERIOD_BY_WAHLPERIODE))
    for sp in (fp, fv, fd, fa):
        sp.add_argument("--force", action="store_true", help="re-download files that already exist")

    sub.add_parser("ingest", help="parse everything under data/raw into the SQLite store")

    q = sub.add_parser("query", help="canned queries; every row carries its source pointer")
    qs = q.add_subparsers(dest="query", required=True)
    for name, needs_person in (
        ("speeches", True),
        ("votes", True),
        ("drucksachen", True),
        ("corpus", False),
    ):
        qp = qs.add_parser(name)
        if needs_person:
            qp.add_argument("--person", required=True, help="MdB id or name")
        _add_range(qp)
        qp.add_argument("--json", action="store_true", help="JSON lines instead of text")
    return p


def cmd_fetch(args: argparse.Namespace) -> None:
    with raw.client() as http:
        if args.source == "stammdaten":
            print(fetch_bundestag.fetch_stammdaten(http))
        elif args.source == "protocols":
            for path in fetch_bundestag.fetch_protocols(http, args.wp, args.first, args.last, force=args.force):
                print(path)
        elif args.source == "votes":
            for row in fetch_bundestag.fetch_votes(http, args.start, args.end, force=args.force):
                print(f"{row['date']} #{row['number']} {row['title']}")
        elif args.source == "dip":
            fetch_dip.fetch_range(http, args.wp, args.start, args.end, force=args.force)
        elif args.source == "aw":
            fetch_aw.fetch_wahlperiode(http, args.wp, force=args.force)


def cmd_query(args: argparse.Namespace) -> None:
    conn = db.connect(db_path())
    start, end = args.start.isoformat(), args.end.isoformat()
    if args.query == "corpus":
        rows = queries.corpus(conn, start, end)
    else:
        person = queries.resolve_person(conn, args.person)
        rows = getattr(queries, args.query)(conn, person["id"], start, end)
        if not args.json:
            print(
                f"# {person['first_name']} {person['last_name']} ({person['id']}), {start}..{end}: {len(rows)} rows\n"
            )
    if args.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return
    for r in rows:
        print(_format(args.query, r))


def _format(kind: str, r: dict) -> str:
    src = f"    ↳ {r['source_document_id']} — {r['source_url']}"
    if kind == "speeches":
        head = f"{r['date']} {r['sitting_id']} · {r['top_id']}: {(r['agenda_title'] or '')[:100]}"
        return f"{head}\n  {r['speaker_name']} [{r['fraction'] or r['speaker_role']}]\n  {r['text'][:300]}…\n{src}\n"
    if kind == "votes":
        line = r["fraction_line"]
        own = f"own vote: {r['own_vote']}" if r["own_vote"] else "not a member at the time / not in list"
        fl = f"; {line['fraction']} majority: {line['majority']} {line['counts']}" if line else ""
        res = r["result"]
        return (
            f"{r['date']} {r['vote_id']} {r['title']} (Drs. {r['drucksache_number'] or '?'})\n"
            f"  result: {res['yes']} yes / {res['no']} no / {res['abstain']} abstain; {own}{fl}\n{src}\n"
        )
    if kind == "drucksachen":
        return (
            f"{r['date']} BT-Drs. {r['number']} [{r['type']}] {r['title'][:120]}\n"
            f"  as: {r['activity_type']}; {r['author_count']} authors\n{src}\n"
        )
    who = f"{r['person_id']} {r['first_name']} {r['last_name']} [{r['fraction'] or r['speaker_role']}]"
    return f"{r['date']} {r['sitting_id']} {who} {len(r['text'])} chars\n{src}"


def main(argv: list[str] | None = None) -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "ingest":
        ingest.ingest_all(db.connect(db_path()))
    elif args.command == "query":
        cmd_query(args)


if __name__ == "__main__":
    main()
