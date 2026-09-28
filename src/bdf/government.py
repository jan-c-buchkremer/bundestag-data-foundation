"""The federal government roster, merged from three sources into ``government_role``.

Sources, in order of priority for the dates:
1. ``wikidata``: "position held" statements (``parse_wikidata``), appointment dates.
2. ``stammdaten``: MdB memberships of kind other whose function is a government office ("Parlamentarischer
   Staatssekretär" at "Bundesministerium der Finanzen"), appointment dates.
3. ``protocol``: the role printed for a speaker in the Plenarprotokolle ("Parl. Staatssekretärin bei der
   Bundesministerin für …"). Its dates are the first and last sitting the role is printed for that person:
   evidence that the person held the office then, not the appointment or its end.

One row per person + kind + department. The row takes its dates and provenance from the source with the highest
priority; ``source_kind`` names it. An open role of a single-holder office (Kanzler, Bundesminister) from Wikidata
or the Stammdaten is closed the day before the protocols first show someone else in that office.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from bdf.parse_wikidata import KINDS

SOURCES = ("wikidata", "stammdaten", "protocol")
SINGLE_HOLDER = ("kanzler", "minister")
CHANCELLERY = "Bundeskanzleramt"
FOREIGN_OFFICE = "Auswärtiges Amt"

_TITLE = re.compile(
    r"^(?:(?P<kanzler>Bundeskanzler(?:in)?)"
    r"|(?P<minister>Bundesminister(?:in)?)"
    r"|(?P<parl_sts>Parl(?:\.|amentarische[r]?) Staatssekretär(?:in)?)"
    r"|(?P<staatsminister>Staatsminister(?:in)?)"
    r"|(?P<beamteter_sts>(?:[Bb]eamtete[r]? )?Staatssekretär(?:in)?))"
    r"(?:\s+(?P<rest>.+))?$"
)
_AT_MINISTER = re.compile(r"^(?:beim|bei der) Bundesminister(?:in)? (?P<of>.+)$")
_AT_CHANCELLOR = re.compile(r"^(?:beim|bei der) Bundeskanzler(?:in)?$")
_IN_DEPARTMENT = re.compile(r"^(?:im|in der) (?P<dept>.+)$")
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


@dataclass(frozen=True)
class ParsedRole:
    kind: str
    department: str | None  # None: the text names no department (Stammdaten titles, "beamteter Staatssekretär")


def _ministry(of: str) -> str:
    """The department of "Bundesminister <of>": "der Finanzen" -> "Bundesministerium der Finanzen"."""
    if of.startswith("des Auswärtigen"):
        return FOREIGN_OFFICE
    if of == "für besondere Aufgaben":  # in this government: the Chef des Bundeskanzleramtes
        return CHANCELLERY
    return f"Bundesministerium {of}"


def parse_role(text: str) -> ParsedRole | None:
    """Kind and department of a federal government office as printed ("Bundesministerin für Gesundheit",
    "Parl. Staatssekretär beim Bundesminister der Finanzen", "Staatsminister für Kultur und Medien") or as a
    Stammdaten function ("Parlamentarische Staatssekretärin"). None for anything else, including Land offices
    ("Staatsminister (Hessen)") and Beauftragte."""
    m = _TITLE.match(text.strip())
    if not m:
        return None
    kind = next(k for k in KINDS if m.group(k))
    rest = m.group("rest")
    if kind == "kanzler":
        return ParsedRole(kind, CHANCELLERY)  # "Bundeskanzler der Bundesrepublik Deutschland" too
    if rest is None:
        return ParsedRole(kind, None)
    if kind == "minister":
        return ParsedRole(kind, _ministry(rest))
    if _AT_CHANCELLOR.match(rest):
        return ParsedRole(kind, CHANCELLERY)
    if at := _AT_MINISTER.match(rest):
        return ParsedRole(kind, _ministry(at["of"]))
    if kind == "beamteter_sts" and (inside := _IN_DEPARTMENT.match(rest)):
        dept = inside["dept"]
        return ParsedRole(kind, FOREIGN_OFFICE if dept == "Auswärtigen Amt" else dept)
    if kind == "staatsminister" and rest == "für Kultur und Medien":
        return ParsedRole(kind, CHANCELLERY)  # BKM, Staatsminister beim Bundeskanzler
    return None


def department_key(name: str | None) -> str:
    """Spelling-tolerant key: "Bundesministerium der Justiz und für Verbraucherschutz" and the Stammdaten's
    "… der Justiz und Verbraucherschutz" -> "bundesministerium-der-justiz-verbraucherschutz"."""
    words = re.findall(r"[a-zäöüß]+", (name or "").lower())
    return "-".join(w.translate(_TRANSLIT) for w in words if w not in ("für", "und"))


def _minister_title(department: str) -> str:
    if department == CHANCELLERY:
        return "Bundeskanzler"
    if department == FOREIGN_OFFICE:
        return "Bundesminister des Auswärtigen"
    return "Bundesminister " + department.removeprefix("Bundesministerium ")


def office_label(kind: str, department: str | None) -> str:
    """Generic office name for a row without a Wikidata label, in Wikidata's style (generic masculine)."""
    if kind == "kanzler":
        return "Bundeskanzler"
    if department is None:
        return {"minister": "Bundesminister", "staatsminister": "Staatsminister",
                "parl_sts": "Parlamentarischer Staatssekretär", "beamteter_sts": "Staatssekretär"}[kind]  # fmt: skip
    if kind == "minister":
        return "Chef des Bundeskanzleramtes" if department == CHANCELLERY else _minister_title(department)
    if kind == "beamteter_sts":
        return f"Staatssekretär ({department})"
    title = "Staatsminister" if kind == "staatsminister" else "Parlamentarischer Staatssekretär"
    return f"{title} beim {_minister_title(department)}"


@dataclass
class Evidence:
    """One source's statement that a person held an office from … to …."""

    source_kind: str  # wikidata | stammdaten | protocol
    person_id: str
    name: str
    kind: str
    department: str | None
    from_date: str
    to_date: str | None
    provenance: dict[str, str]
    wikidata_qid: str | None = None
    office: str | None = None  # Wikidata's position label; the others get office_label()
    id: str | None = None  # Wikidata statement id

    @property
    def key(self) -> tuple[str, str, str]:
        return self.person_id, self.kind, department_key(self.department)


@dataclass
class Inference:
    row_id: str
    name: str
    office: str
    source_kind: str
    from_date: str
    to_date: str
    successor: str
    successor_from: str
    successor_document: str

    def __str__(self) -> str:
        return (
            f"{self.name}, {self.office} ({self.source_kind}, from {self.from_date}, no end): closed {self.to_date}, "
            f"the day before {self.successor} first speaks in that office ({self.successor_from}, "
            f"{self.successor_document})"
        )


def _day_before(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def _combine(items: list[Evidence]) -> Evidence:
    """Several statements of one source for the same person + office: earliest start, latest (or open) end."""
    first = min(items, key=lambda e: e.from_date)
    ends = [e.to_date for e in items]
    to_date = None if None in ends else max(e for e in ends if e)
    return Evidence(**{**first.__dict__, "to_date": to_date})


def merge(evidence: list[Evidence]) -> tuple[list[dict], list[Inference]]:
    """government_role rows (without the person-independent columns filled by the caller) and the inferences made."""
    # department names: Wikidata's, else the one derived from the protocol text, else the Stammdaten institution
    names: dict[str, str] = {}
    for source in ("wikidata", "protocol", "stammdaten"):
        for e in evidence:
            if e.source_kind == source and e.department:
                names.setdefault(department_key(e.department), e.department)
    groups: dict[tuple[str, str, str], dict[str, list[Evidence]]] = {}
    for e in evidence:
        groups.setdefault(e.key, {}).setdefault(e.source_kind, []).append(e)
    rows = []
    for (pid, kind, dkey), by_source in groups.items():
        source = next(s for s in SOURCES if s in by_source)
        chosen = _combine(by_source[source])
        wikidata = _combine(by_source["wikidata"]) if "wikidata" in by_source else None
        department = names.get(dkey) if dkey else None
        rows.append(
            {
                "id": chosen.id if source == "wikidata" else f"{source}:{pid}:{kind}:{dkey or '-'}",
                "person_id": pid,
                "wikidata_qid": wikidata.wikidata_qid if wikidata else chosen.wikidata_qid,
                "name": wikidata.name if wikidata else chosen.name,
                "office": wikidata.office if wikidata and wikidata.office else office_label(kind, department),
                "department": department,
                "kind": kind,
                "from_date": chosen.from_date,
                "to_date": chosen.to_date,
                "source_kind": source,
                **chosen.provenance,
            }
        )
    inferences = []
    for row in rows:
        if row["kind"] not in SINGLE_HOLDER or row["source_kind"] == "protocol" or row["to_date"] is not None:
            continue
        dkey = department_key(row["department"])
        later = sorted(
            (e for e in evidence
             if e.source_kind == "protocol" and e.kind == row["kind"] and dkey and e.key[2] == dkey
             and e.person_id != row["person_id"] and e.from_date > row["from_date"]),
            key=lambda e: e.from_date,
        )  # fmt: skip
        if later:
            successor = later[0]
            row["to_date"] = _day_before(successor.from_date)
            inferences.append(
                Inference(
                    row_id=row["id"], name=row["name"], office=row["office"], source_kind=row["source_kind"],
                    from_date=row["from_date"], to_date=row["to_date"], successor=successor.name,
                    successor_from=successor.from_date, successor_document=successor.provenance["source_document_id"],
                )
            )  # fmt: skip
    rows.sort(key=lambda r: (KINDS.index(r["kind"]), r["from_date"], r["department"] or "", r["name"]))
    return rows, inferences


def held_on(rows: list[dict], on: str) -> list[dict]:
    """The roles held on ``on``. A protocol row's ``to_date`` is only the last sitting that shows the role, so it
    counts as held after that until contradicted: by a later role of the same person, or, for a Kanzler or
    Bundesminister, by a later holder of the same office."""

    def contradicted(r: dict) -> bool:
        return any(
            r["to_date"] < h["from_date"] <= on
            and (
                h["person_id"] == r["person_id"]
                or (r["kind"] in SINGLE_HOLDER and h["kind"] == r["kind"] and h["department"] == r["department"])
            )
            for h in rows
            if h is not r
        )

    return [
        r
        for r in rows
        if r["from_date"] <= on
        and (r["to_date"] is None or r["to_date"] >= on or (r["source_kind"] == "protocol" and not contradicted(r)))
    ]
