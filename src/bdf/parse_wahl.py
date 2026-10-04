"""Parse the Bundeswahlleiterin's election CSVs (semicolon, UTF-8 with BOM, a preamble before the header).

- ``*_gewaehlte_utf8.csv``: one row per elected candidate with the way they were elected (Kennzeichen), the
  area (Wahlkreis or Land), the first-vote share of a constituency winner, the list position, and the linked
  second candidacy (Verkn…): the list of a constituency winner, or the Wahlkreis a list candidate stood in.
- ``kerg2.csv``: results per area, party and vote (Stimme 1 = Erststimme, 2 = Zweitstimme) in long format. On
  Wahlkreis rows, ``Gewählt`` names the party whose candidate got the seat, or "–" when the winner got none
  (since the 2023 reform a constituency winner needs Zweitstimmendeckung).
- ``*_nachfolger.pdf``: "Veränderungen im 21. Deutschen Bundestag", one table row per departed member and their
  successor: name, birth year, occupation, "Partei, Land, LL/WK nnn", the reason, the successor likewise and the
  start of the membership. Occupations wrap onto the lines around a row; everything else is on the row's line.
- ``*_wkr_gemeinden_*_utf8.csv``: the Wahlkreiseinteilung, one row per Gemeinde and Wahlkreis. A Gemeinde split
  across Wahlkreise (18 in 2025: Hamburg, Berlin and big cities) has one row per Wahlkreis with a
  running Gemeindeteil number and the range Wahlkreis-von/-bis; the Gebietsstand is in a "# Gebietsstand:" line.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# Land numbers in the Bundeswahlleiterin's files (the official order)
LAND = {
    1: "SH", 2: "HH", 3: "NI", 4: "HB", 5: "NW", 6: "HE", 7: "RP", 8: "BW",
    9: "BY", 10: "SL", 11: "BE", 12: "BB", 13: "MV", 14: "SN", 15: "ST", 16: "TH",
}  # fmt: skip
NO_SEAT = "–"


@dataclass
class Table:
    rows: list[dict[str, str]]
    as_of: str  # "Stand" line, ISO date


def read_table(path: Path) -> Table:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    as_of = ""
    for i, line in enumerate(lines):
        cells = line.split(";")
        if cells[0] == "Stand:":
            d, m, y = cells[1].split(".")
            as_of = f"{y}-{m}-{d}"
        if cells[0] == "Wahlart":
            header = cells
            rows = [dict(zip(header, r.split(";"), strict=False)) for r in lines[i + 1 :] if r.strip(";")]
            return Table(rows, as_of)
    raise ValueError(f"{path}: no header row (Wahlart;…)")


def number(s: str) -> float | None:
    """German decimal ("38,263032") or None for an empty cell."""
    return float(s.replace(",", ".")) if s else None


def integer(s: str) -> int | None:
    return int(s) if s else None


def parse_gewaehlte(path: Path) -> tuple[list[dict], str]:
    """Elected candidates, one dict per row, in the file's order; and the file's "Stand" date."""
    table = read_table(path)
    out = []
    for r in table.rows:
        direct = r["Kennzeichen"] == "Kreiswahlvorschlag"
        linked = r["VerknKennzeichen"]
        if direct:  # won the Wahlkreis; the linked candidacy, if any, is a Landesliste
            wk, lst = int(r["Gebietsnummer"]), linked == "Landesliste"
            state, position = (r["VerknGebietLandAbk"], integer(r["VerknListenplatz"])) if lst else (None, None)
        else:  # elected from the Landesliste; the linked candidacy, if any, is a Wahlkreis
            wk = integer(r["VerknGebietsnummer"]) if linked == "Kreiswahlvorschlag" else None
            state, position = r["GebietLandAbk"], integer(r["Listenplatz"])
        out.append(
            {
                "last_name": r["Nachname"],
                "first_names": r["Vornamen"],
                "academic_title": r["Titel"] or None,
                "name_prefix": r["Namenszusatz"] or None,
                "gender": r["Geschlecht"] or None,
                "birth_year": integer(r["Geburtjahr"]),
                "birth_place": r["Geburtsort"] or None,
                "occupation": r["Beruf"] or None,
                "party": r["Gruppenname"],
                "elected_via": "constituency" if direct else "list",
                "constituency_number": wk,
                "first_vote_percent": number(r["Prozent"]) if direct else None,
                "list_state": state,
                "list_position": position,
            }
        )
    return out, table.as_of


def parse_kerg2(path: Path) -> tuple[list[dict], list[dict], str]:
    """Wahlkreise (with electorate, voters and the party that got the seat) and their results per party and vote."""
    table = read_table(path)
    constituencies: dict[int, dict] = {}
    results = []
    for r in table.rows:
        if r["Gebietsart"] != "Wahlkreis":
            continue
        nr = int(r["Gebietsnummer"])
        c = constituencies.setdefault(
            nr,
            {
                "number": nr,
                "name": r["Gebietsname"],
                "state": LAND[int(r["UegGebietsnummer"])],
                "seat_party": None if r["Gewählt"] in (NO_SEAT, "") else r["Gewählt"],
                "electorate": None,
                "voters": None,
            },
        )
        if r["Gruppenart"] == "System-Gruppe":
            if r["Gruppenname"] == "Wahlberechtigte":
                c["electorate"] = integer(r["Anzahl"])
            elif r["Gruppenname"] == "Wählende":
                c["voters"] = integer(r["Anzahl"])
            continue
        if not r["Stimme"] or not r["Anzahl"]:
            continue  # the party had no candidate or no list here
        results.append(
            {
                "constituency_number": nr,
                "group_order": int(r["Gruppenreihenfolge"]),
                "group_kind": "party" if r["Gruppenart"] == "Partei" else "individual",
                "party": r["Gruppenname"],
                "vote": int(r["Stimme"]),
                "votes": int(r["Anzahl"]),
                "percent": number(r["Prozent"]),
            }
        )
    return list(constituencies.values()), results, table.as_of


def parse_gemeinden(path: Path) -> tuple[list[dict], str]:
    """Gemeinden per Wahlkreis with the 8-digit Amtlicher Gemeindeschlüssel (Land, RegBez, Kreis, Gemeinde);
    and the file's Gebietsstand as ISO date."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    as_of, header, out = "", None, []
    for line in lines:
        cells = [c.strip() for c in line.split(";")]
        if header is None:
            if cells[0].startswith("# Gebietsstand:"):
                d, m, y = cells[0].split(":")[1].strip().split(".")
                as_of = f"{y}-{m}-{d}"
            elif cells[0] == "Wahlkreis-Nr":
                header = cells
            continue
        if not line.strip(";"):
            continue
        r = dict(zip(header, cells, strict=False))
        out.append(
            {
                "ags": r["RGS_Land"] + r["RGS_RegBez"] + r["RGS_Kreis"] + r["RGS_Gemeinde"],
                "name": r["Gemeindename"],
                "district": r["Kreisname"],
                "state": LAND[int(r["RGS_Land"])],
                "constituency_number": int(r["Wahlkreis-Nr"]),
                "split": bool(r["Wahlkreis-von"]),
            }
        )
    if header is None:
        raise ValueError(f"{path}: no header row (Wahlkreis-Nr;…)")
    return out, as_of


# the parties of the Bundestag as the Nachfolger list prints them
_PARTY = r"(?:CDU|CSU|SPD|AfD|GRÜNE|Die Linke|FDP|BSW|SSW)"
# Land abbreviations of the Nachfolger list that differ from LAND's
_LIST_LAND = {"NRW": "NW"}
_SIDE = (
    r"(?P<{p}name>[^,\d]+?, [^\d]+?) (?P<{p}year>\d{{4}}) (?:.*? )?"
    r"(?P<{p}party>" + _PARTY + r"), (?P<{p}state>[A-Z]{{2,3}}), (?P<{p}seat>(?:LL|WK) \d{{3}})"
)
_SUCCESSION = re.compile(
    r"^(?P<n>\d+) "
    + _SIDE.format(p="pre_")
    + r" (?P<reason>\S+) "
    + _SIDE.format(p="")
    + r" (?P<date>\d{2}\.\d{2}\.\d{4})$"
)


_ROW_LIKE = re.compile(r"^\d+ .*\d{2}\.\d{2}\.\d{4}$")


def parse_successors(path: Path) -> tuple[list[dict], list[str]]:
    """The Mandatsnachfolger list: one dict per row, in the list's order; and the lines that look like a row
    (a number first, a date last) but did not parse, for the caller to report."""
    import pdfplumber  # only needed here and for protocol PDFs

    out, unparsed = [], []
    with pdfplumber.open(path) as pdf:
        lines = [line.strip() for page in pdf.pages for line in (page.extract_text() or "").splitlines()]
    for line in lines:
        m = _SUCCESSION.match(line)
        if m is None:
            if _ROW_LIKE.match(line):
                unparsed.append(line)
            continue
        d, mo, y = m["date"].split(".")
        out.append(
            {
                "number": int(m["n"]),
                "predecessor_name": m["pre_name"], "predecessor_party": m["pre_party"],
                "predecessor_state": _LIST_LAND.get(m["pre_state"], m["pre_state"]),
                "predecessor_seat": m["pre_seat"], "reason": m["reason"],
                "name": m["name"], "birth_year": int(m["year"]), "party": m["party"],
                "state": _LIST_LAND.get(m["state"], m["state"]), "seat": m["seat"], "from_date": f"{y}-{mo}-{d}",
            }
        )  # fmt: skip
    return out, unparsed


def document_id(kind: str, election: str, as_of: str) -> str:
    """Citable source, e.g. "Bundeswahlleiterin, BTW 2025 Gewählte (Stand 2025-03-12)"."""
    year = "20" + re.sub(r"\D", "", election)
    return f"Bundeswahlleiterin, BTW {year} {kind} (Stand {as_of})"
