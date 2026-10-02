"""bdf: fetch raw sources, ingest them into SQLite, run canned queries."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from bdf import db, fetch_aw, fetch_bundestag, fetch_dip, fetch_wahl, fetch_wikidata, ingest, queries, raw, update
from bdf.config import db_path
from bdf.export import write_export


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
    fp.add_argument("--from", dest="first", type=int, metavar="NR")
    fp.add_argument("--to", dest="last", type=int, metavar="NR")
    fp.add_argument(
        "--preliminary",
        action="store_true",
        help="instead of a range: fetch the preliminary protocols on disk again, and the final PDF of each",
    )
    fv = fs.add_parser("votes", help="roll-call vote XLSX/PDF by date range")
    _add_range(fv)
    fd = fs.add_parser("dip", help="DIP Drucksachen, authors, Vorgänge, Vorgangspositionen, persons by date range")
    fd.add_argument("--wp", type=int, default=21)
    _add_range(fd)
    fa = fs.add_parser("aw", help="abgeordnetenwatch mandates and politicians of a Wahlperiode")
    fa.add_argument("--wp", type=int, default=21, choices=sorted(fetch_aw.PERIOD_BY_WAHLPERIODE))
    fw = fs.add_parser("wahl", help="Bundeswahlleiterin: elected candidates and results per Wahlkreis")
    fw.add_argument("--election", default="btw25", choices=sorted(fetch_wahl.ELECTIONS))
    fph = fs.add_parser("photos", help="bundestag.de MdB biography list (all pages) and the portraits")
    fs.add_parser("government", help="Wikidata: federal government roles since 2025-05-06, Commons portraits")
    for sp in (fp, fv, fd, fa, fw, fph):
        sp.add_argument("--force", action="store_true", help="re-download files that already exist")

    sub.add_parser("ingest", help="parse everything under data/raw into the SQLite store")

    e = sub.add_parser("export", help="open-data export: one CSV.gz per table, datapackage.json, README.md")
    e.add_argument("dir", type=Path, help="target directory (replaced as a whole when the export succeeds)")

    u = sub.add_parser("update", help="fetch everything new since the last run from all sources, then ingest")
    u.add_argument("--wp", type=int, default=21, choices=sorted(update.WP_START))

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
    qg = qs.add_parser("government", help="government roles (Wikidata, Stammdaten, protocols) with person and source")
    qg.add_argument("--date", type=_date, help="only roles held on this day (default: all since 2025-05-06)")
    qph = qs.add_parser("photos", help="portraits with credit; persons of the Wahlperiode without one")
    qph.add_argument("--missing", action="store_true", help="list sitting members without a portrait instead")
    qs_ = qs.add_parser(
        "stale-roles", help="protocol-only government roles not printed in a protocol for a while (still current)"
    )
    qs_.add_argument(
        "--days", type=int, default=queries.STALE_AFTER_DAYS, help="days before the newest sitting (default: 90)"
    )
    qgap = qs.add_parser(
        "protocol-gaps", help="preliminary protocols and DIP Beratungen without an agenda item in the protocol"
    )
    for sp in (qg, qph, qs_, qgap):
        sp.add_argument("--json", action="store_true", help="JSON lines instead of text")
    qd = qs.add_parser("decisions", help="decisions announced by the chair in one sitting")
    qd.add_argument("--sitting", required=True, help='sitting id, e.g. "21/90"')
    qd.add_argument("--json", action="store_true", help="JSON lines instead of text")
    return p


def cmd_fetch(args: argparse.Namespace) -> None:
    with raw.client() as http:
        if args.source == "stammdaten":
            print(fetch_bundestag.fetch_stammdaten(http))
        elif args.source == "protocols" and args.preliminary:
            for path, still in fetch_bundestag.refetch_preliminary(http, args.wp):
                print(f"{path}: {'still preliminary' if still else 'final version now'}")
            for path, changed in fetch_bundestag.fetch_preliminary_pdfs(http, args.wp):
                print(f"{path}: {'new or changed' if changed else 'unchanged'}")
        elif args.source == "protocols":
            if args.first is None or args.last is None:
                raise SystemExit("bdf fetch protocols: --from and --to are required (or --preliminary)")
            for path in fetch_bundestag.fetch_protocols(http, args.wp, args.first, args.last, force=args.force):
                print(path)
        elif args.source == "votes":
            for row in fetch_bundestag.fetch_votes(http, args.start, args.end, force=args.force):
                print(f"{row['date']} #{row['number']} {row['title']}")
        elif args.source == "dip":
            fetch_dip.fetch_range(http, args.wp, args.start, args.end, force=args.force)
        elif args.source == "aw":
            fetch_aw.fetch_wahlperiode(http, args.wp, force=args.force)
        elif args.source == "wahl":
            for path in fetch_wahl.fetch_election(http, args.election, force=args.force):
                print(path)
        elif args.source == "photos":
            cards = fetch_bundestag.fetch_biografien(http, force=args.force)
            print(f"{len(cards)} biography cards, portraits in {fetch_bundestag.fotos_dir()}")
        elif args.source == "government":
            roles = fetch_wikidata.fetch_government(http)
            print(f"{len(roles)} result rows in {fetch_wikidata.government_path()}")


def cmd_query(args: argparse.Namespace) -> None:
    conn = db.connect(db_path())
    if args.query in ("government", "photos", "stale-roles", "protocol-gaps"):
        if args.query == "protocol-gaps":
            rows = queries.protocol_gaps(conn)
        elif args.query == "government":
            rows = queries.government(conn, args.date.isoformat() if args.date else None)
        elif args.query == "stale-roles":
            rows = queries.stale_roles(conn, args.days)
        else:
            rows = queries.photos_missing(conn) if args.missing else queries.photos(conn)
        for r in rows:
            print(json.dumps(r, ensure_ascii=False) if args.json else _format(args.query, r))
        return
    if args.query == "decisions":
        rows = queries.decisions(conn, args.sitting)
        if not args.json:
            print(f"# sitting {args.sitting}: {len(rows)} decisions\n")
    elif args.query == "corpus":
        rows = queries.corpus(conn, args.start.isoformat(), args.end.isoformat())
    else:
        start, end = args.start.isoformat(), args.end.isoformat()
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


def cmd_export(args: argparse.Namespace) -> None:
    files = write_export(db.connect(db_path()), args.dir)
    for f in files:
        rows = f"{f['rows']:>10,} rows" if f["rows"] is not None else " " * 15
        print(f"{f['name']:<32}{rows}  {f['bytes'] / 1e6:8.2f} MB")
    print(f"{'total':<32}{'':15}  {sum(f['bytes'] for f in files) / 1e6:8.2f} MB in {args.dir}")


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
    if kind == "government":
        who = f"{r['name']} ({r['person_id']}{', MdB' if r['is_mdb'] else ''})"
        if r["source_kind"] == "protocol":
            when = f"belegt {r['from_date']}..{r['to_date']} (Plenarprotokoll)"
        else:
            when = f"{r['from_date']}..{r['to_date'] or 'today'} ({r['source_kind']})"
        return f"{when} [{r['kind']}] {r['office']}: {who}\n{src}"
    if kind == "stale-roles":
        return (
            f"last seen {r['to_date']} ({r['days_since_seen']} days before the newest sitting {r['newest_sitting']}), "
            f"belegt ab {r['from_date']} [{r['kind']}] {r['office']}: {r['name']} ({r['person_id']})\n{src}"
        )
    if kind == "protocol-gaps":
        head = f"{r['sitting_id']} ({r['date']})"
        if r["preliminary"]:
            head += f": PRELIMINARY (final announced for {r['final_announced'] or '?'})"
            head += (
                f", XML ends on page {r['last_page']}, {r['pdf_speeches']} speeches from the PDF"
                if r["last_page"]
                else ", no PDF part"
            )
        lines = [head] + [
            f"  [{b['cause']}] {b['position']}, S. {b['pages']}: {(b['title'] or b['vorgang_id'])[:100]}"
            for b in r["beratungen"]
        ]
        return "\n".join(lines) + f"\n    ↳ {r['source_document_id']} — {r['pdf_url']}"
    if kind == "photos":
        if "local_path" not in r:  # --missing
            return f"{r['person_id']} {r['first_name']} {r['last_name']} [{r['fraction'] or '?'}]"
        return f"{r['person_id']} {r['first_name']} {r['last_name']}: {r['local_path']} (© {r['credit']})\n{src}"
    if kind == "decisions":
        how = (
            ", ".join(f"{f} {p}" for f, p in r["fractions"].items())
            if r["kind"] == "handzeichen"
            else (f"{r['roll_call']['yes']} yes / {r['roll_call']['no']} no / {r['roll_call']['abstain']} abstain"
                  f" ({r['roll_call_vote_id']})" if r["roll_call"] else "roll-call vote not in the store")
        )  # fmt: skip
        return (
            f"{r['decision_id']} [{r['kind']}] {r['top_id'] or '?'} · {r['subject'][:110]} "
            f"(Drs. {r['drucksache_number'] or '?'})\n  {r['result'] or 'result not found'}: {how}\n{src}\n"
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
    elif args.command == "update":
        sys.exit(update.run(args.wp))
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
