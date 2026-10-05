"""Open-data export: one gzipped CSV per table, a Frictionless datapackage.json and a README.

The export is written to a temporary sibling directory and renamed into place at the end, so a failed run
leaves the previous export (or nothing) behind, never a half-written one.
"""

import csv
import gzip
import io
import json
import os
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from bdf import db

REPO_URL = "https://github.com/jan-c-buchkremer/bundestag-data-foundation"

# Columns that only make sense on the machine that built the store
EXCLUDED_COLUMNS = {("person_photo", "local_path")}

PROVENANCE_DESCRIPTIONS = {
    "source_url": "where the row was read from (document, file or API record)",
    "source_document_id": 'citable source pointer, e.g. "BT-PlPr. 21/94", "BT-Drs. 21/7300"',
    "retrieved_at": "when the source was downloaded (ISO 8601, UTC)",
}

# Terms per source, summarised from docs/licences.md (not legal advice)
SOURCES = {
    "bundestag": {
        "title": "Deutscher Bundestag: Open Data (Plenarprotokolle, MdB-Stammdaten, Namentliche Abstimmungen)",
        "path": "https://www.bundestag.de/services/opendata",
        "license": {
            "name": "bundestag-nutzungsbedingungen",
            "title": "Nutzungsbedingungen des Deutschen Bundestages; Plenarprotokolle sind amtliche Werke (§ 5 UrhG)",
            "path": "https://www.bundestag.de/nutzungsbedingungen",
        },
        "attribution": 'Quelle: "Deutscher Bundestag", bei Plenarprotokollen mit Nummer (BT-PlPr. 21/94). '
        "Keine Nutzung für Werbezwecke.",
    },
    "dip": {
        "title": "Deutscher Bundestag/Bundesrat: DIP (Dokumentations- und Informationssystem für "
        "Parlamentsmaterialien)",
        "path": "https://dip.bundestag.de",
        "license": {
            "name": "dip-nutzungsbedingungen",
            "title": "Nutzungsbedingungen für das DIP (27.02.2023)",
            "path": f"{REPO_URL}/blob/main/docs/nutzungsbedingungen_dip.pdf",
        },
        "attribution": '"Deutscher Bundestag/Bundesrat – DIP", bei Drucksachen und Plenarprotokollen mit Nummer '
        "(BT-Drs. 21/7300, BT-PlPr. 21/94). Änderungen sind zu kennzeichnen. Bei kommerzieller Nutzung: Hinweis, "
        "dass die Daten unter dip.bundestag.de kostenfrei verfügbar sind.",
    },
    "abgeordnetenwatch": {
        "title": "abgeordnetenwatch.de API v2",
        "path": "https://www.abgeordnetenwatch.de/api",
        "license": {
            "name": "CC0-1.0",
            "title": "CC0 1.0 Universal",
            "path": "https://creativecommons.org/publicdomain/zero/1.0/",
        },
        "attribution": "Keine Pflicht; Nennung von abgeordnetenwatch.de erbeten.",
    },
    "bundeswahlleiterin": {
        "title": "Die Bundeswahlleiterin: Open Data Bundestagswahl 2025",
        "path": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/opendata.html",
        "license": {
            "name": "dl-de-by-2.0",
            "title": "Datenlizenz Deutschland – Namensnennung – Version 2.0",
            "path": "https://www.govdata.de/dl-de/by-2-0",
        },
        "attribution": '"© Die Bundeswahlleiterin, Wiesbaden 2025", mit Link auf die Lizenz; Änderungen sind zu '
        "kennzeichnen.",
    },
    "bundeswahlleiterin-nachfolger": {
        "title": "Die Bundeswahlleiterin: Veränderungen im 21. Deutschen Bundestag (Mandatsnachfolger)",
        "path": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/gewaehlte.html",
        "license": {
            "name": "bundeswahlleiterin-impressum",
            "title": "Impressum der Bundeswahlleiterin: Vervielfältigung und Verbreitung mit Quellennachweis gestattet",
            "path": "https://www.bundeswahlleiterin.de/info/impressum.html",
        },
        "attribution": '"Die Bundeswahlleiterin, Wiesbaden" als Herausgeberin; Änderungen sind zu kennzeichnen.',
    },
    "wikidata": {
        "title": "Wikidata",
        "path": "https://www.wikidata.org",
        "license": {
            "name": "CC0-1.0",
            "title": "CC0 1.0 Universal",
            "path": "https://creativecommons.org/publicdomain/zero/1.0/",
        },
        "attribution": "Keine Pflicht.",
    },
    "portraits": {
        "title": "Porträts: bundestag.de (Abgeordnetenbiografien) und Wikimedia Commons",
        "path": "https://www.bundestag.de/abgeordnete",
        "license": {
            "name": "per-image",
            "title": "Je Bild: Rechteinhaber laut person_photo.credit; Commons-Dateien meist CC BY-SA 4.0 "
            "(Lizenz auf der Dateiseite, source_url)",
            "path": f"{REPO_URL}/blob/main/docs/licences.md#portraits-bundestagde-wikimedia-commons",
        },
        "attribution": "Bei jedem Bild die Angabe aus person_photo.credit; bei Commons-Bildern zusätzlich Lizenz "
        "mit Link auf die Dateiseite. Der Export enthält nur Bild-URLs und Credits, keine Bilder; die Rechte an "
        "den Fotos sind nicht einzeln geprüft.",
    },
}

# Which sources each table's rows come from (see docs/design.md)
TABLE_SOURCES = {
    "person": ["bundestag", "abgeordnetenwatch", "wikidata"],
    "mandate": ["bundestag"],
    "membership": ["bundestag"],
    "sitting": ["bundestag"],
    "agenda_item": ["bundestag"],
    "agenda_item_paragraph": ["bundestag"],
    "agenda_sub_item": ["bundestag"],
    "agenda_item_vorlage": ["bundestag", "dip"],
    "speech": ["bundestag"],
    "speech_paragraph": ["bundestag"],
    "interjection": ["bundestag"],
    "decision": ["bundestag"],
    "decision_fraction": ["bundestag"],
    "roll_call_vote": ["bundestag", "dip"],
    "individual_vote": ["bundestag"],
    "drucksache": ["dip"],
    "drucksache_author": ["dip"],
    "decision_vorgang": ["bundestag", "dip"],
    "question_activity": ["dip"],
    "vorgang": ["dip"],
    "vorgang_drucksache": ["dip"],
    "vorgang_position": ["dip"],
    "vorgang_referral": ["dip"],
    "aw_profile": ["abgeordnetenwatch"],
    "side_job": ["abgeordnetenwatch"],
    "constituency": ["bundeswahlleiterin"],
    "constituency_result": ["bundeswahlleiterin"],
    "constituency_municipality": ["bundeswahlleiterin"],
    "election_candidacy": ["bundeswahlleiterin"],
    "mandate_successor": ["bundeswahlleiterin-nachfolger"],
    "person_photo": ["portraits"],
    "government_role": ["wikidata", "bundestag"],
    "party_fraction": ["bundestag", "bundeswahlleiterin"],  # party names as these sources print them
    "person_alias": ["bundestag", "wikidata"],  # placeholder ids from Wikidata and protocol PDFs
}

_SQL_TYPES = {"TEXT": "string", "INTEGER": "integer", "REAL": "number"}


def column_comments(schema: str = db.SCHEMA) -> dict[tuple[str, str], str]:
    """(table, column) -> the "-- …" comment in the CREATE TABLE statements, continuation lines joined."""
    comments: dict[tuple[str, str], str] = {}
    table = last = None
    for line in schema.splitlines():
        if m := re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", line):
            table, last = m.group(1), None
        elif m := re.match(r"\s+(\w+) [A-Z]+[^-]*?(?:--\s*(.*))?$", line):
            if m.group(1) in ("PRIMARY", "FOREIGN"):
                continue
            last = (table, m.group(1))
            if m.group(2):
                comments[last] = m.group(2).strip()
        elif (m := re.match(r"\s+--\s*(.*)$", line)) and last in comments:
            comments[last] += " " + m.group(1).strip()
    return comments


def _tables(conn: sqlite3.Connection) -> list[str]:
    sql = "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    return [r[0] for r in conn.execute(sql)]


def _resource(conn: sqlite3.Connection, table: str, comments: dict) -> tuple[dict, list[str], list[str]]:
    """Frictionless resource descriptor, the exported columns and the primary key of one table."""
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = [r[1] for r in info if (table, r[1]) not in EXCLUDED_COLUMNS]
    pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    # (column, referenced table, referenced column), single-column keys only
    fks = [(fk[3], fk[2], fk[4]) for fk in conn.execute(f"PRAGMA foreign_key_list({table})") if fk[4]]
    references = {col: f"{ref}.{ref_col}" for col, ref, ref_col in fks}
    fields = []
    for r in info:
        if r[1] not in columns:
            continue
        field = {"name": r[1], "type": _SQL_TYPES.get(r[2].upper(), "string")}
        desc = comments.get((table, r[1])) or PROVENANCE_DESCRIPTIONS.get(r[1])
        if r[1] in references:
            desc = f"→ {references[r[1]]}" + (f"; {desc}" if desc else "")
        if desc:
            field["description"] = desc
        if r[3] or r[1] in pk:
            field["constraints"] = {"required": True}
        fields.append(field)
    schema: dict = {"fields": fields, "primaryKey": pk, "missingValues": [""]}
    if fks:
        schema["foreignKeys"] = [
            {"fields": col, "reference": {"resource": ref, "fields": ref_col}} for col, ref, ref_col in sorted(fks)
        ]
    source_ids = TABLE_SOURCES.get(table, [])
    resource = {
        "name": table,
        "path": f"{table}.csv.gz",
        "format": "csv",
        "mediatype": "text/csv",
        "compression": "gz",
        "encoding": "utf-8",
        "schema": schema,
        "sources": [{"title": SOURCES[s]["title"], "path": SOURCES[s]["path"]} for s in source_ids],
        "licenses": [SOURCES[s]["license"] for s in source_ids],
    }
    return resource, columns, pk


def _write_csv(conn: sqlite3.Connection, table: str, columns: list[str], pk: list[str], path: Path) -> int:
    """Write the table ordered by primary key; NULL becomes an empty field. Returns the row count."""
    cols = ", ".join(columns)
    order = ", ".join(pk) if pk else "rowid"
    n = 0
    # mtime=0: the same rows give the same bytes
    with (
        open(path, "wb") as fh,
        gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=6) as gz,
        io.TextIOWrapper(gz, encoding="utf-8", newline="") as f,
    ):
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        for row in conn.execute(f"SELECT {cols} FROM {table} ORDER BY {order}"):
            w.writerow(["" if v is None else v for v in row])
            n += 1
    return n


def write_export(conn: sqlite3.Connection, target: Path) -> list[dict]:
    """Export every table of the store to `target`. Returns one {name, rows, bytes} per file written."""
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        comments = column_comments()
        resources, summary = [], []
        for table in _tables(conn):
            resource, columns, pk = _resource(conn, table, comments)
            path = tmp / resource["path"]
            rows = _write_csv(conn, table, columns, pk, path)
            resource["bytes"] = path.stat().st_size
            resources.append(resource)
            summary.append({"name": resource["path"], "rows": rows, "bytes": resource["bytes"]})
        latest = conn.execute("SELECT max(date) FROM sitting").fetchone()[0]
        package = _package(resources, latest)
        (tmp / "datapackage.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", "utf-8")
        (tmp / "README.md").write_text(_readme(package, summary), "utf-8")
        for name in ("datapackage.json", "README.md"):
            summary.append({"name": name, "rows": None, "bytes": (tmp / name).stat().st_size})
        _replace(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return summary


def _replace(tmp: Path, target: Path) -> None:
    """Move tmp to target. A directory cannot be renamed onto a non-empty one, so the old export is moved aside
    first; the window without an export is two renames long."""
    old = target.parent / f".{target.name}.old-{os.getpid()}"
    if target.exists():
        target.rename(old)
    tmp.rename(target)
    shutil.rmtree(old, ignore_errors=True)


def _package(resources: list[dict], latest_sitting: str | None) -> dict:
    used = [s for s in SOURCES if any(s in TABLE_SOURCES.get(r["name"], []) for r in resources)]
    return {
        "profile": "data-package",
        "name": "bundestag-data-foundation",
        "title": "Bundestag: Plenarprotokolle, Abstimmungen, Drucksachen, Abgeordnete (21. Wahlperiode)",
        "description": "Export of the bundestag-data-foundation SQLite store: one gzipped CSV per table. Every row "
        "that comes from a source carries source_url, source_document_id and retrieved_at.",
        "homepage": REPO_URL,
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_sitting_date": latest_sitting,
        "sources": [
            {"title": SOURCES[s]["title"], "path": SOURCES[s]["path"], "attribution": SOURCES[s]["attribution"]}
            for s in used
        ],
        "licenses": list({SOURCES[s]["license"]["name"]: SOURCES[s]["license"] for s in used}.values()),
        "resources": resources,
    }


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _readme(package: dict, summary: list[dict]) -> str:
    tables = "\n".join(f"| `{s['name']}` | {s['rows']:,} | {_size(s['bytes'])} |".replace(",", ".") for s in summary)
    sources = "\n".join(
        f"- **{s['title']}** ({s['path']}), Lizenz: {s['license']['title']} ({s['license']['path']}).  \n"
        f"  {s['attribution']}"
        for s in SOURCES.values()
        if s["title"] in {p["title"] for p in package["sources"]}
    )
    return f"""# Bundestag-Daten: offener Export

Stand: {package["created"]} · letzte Sitzung im Export: {package["latest_sitting_date"] or "–"}

Alle Tabellen der [bundestag-data-foundation]({REPO_URL}) als gzip-komprimierte CSV-Dateien (UTF-8, Kopfzeile,
nach Primärschlüssel sortiert; leeres Feld = kein Wert). `datapackage.json` beschreibt jede Tabelle nach dem
[Frictionless-Data-Package-Standard](https://datapackage.org/): Spalten mit Typ und Beschreibung (englisch),
Primär- und Fremdschlüssel, Quellen und Lizenzen je Tabelle.

Jede Zeile, die aus einer Quelle stammt, nennt sie: `source_url` (Dokument oder API-Datensatz),
`source_document_id` (zitierfähig, z. B. „BT-PlPr. 21/94“, „BT-Drs. 21/7300“) und `retrieved_at`.

| Datei | Zeilen | Größe |
|---|---:|---:|
{tables}

## Quellen und Pflichtangaben

Wer die Daten weitergibt oder veröffentlicht, nennt je Quelle, was dort verlangt ist:

{sources}

Zusammenfassung der Nutzungsbedingungen, keine Rechtsberatung. Einzelheiten:
[docs/licences.md]({REPO_URL}/blob/main/docs/licences.md). Datenmodell und Spaltenbedeutung:
[docs/design.md]({REPO_URL}/blob/main/docs/design.md).

Die Daten werden automatisch aus den Quellen gelesen (Protokoll-XML, XLSX, APIs); Fehler beim Einlesen sind möglich.
Maßgeblich ist das Originaldokument unter `source_url`.
"""
