"""Parse the Wikidata government roster (SPARQL JSON) and the Commons imageinfo of its portraits.

One role per "position held" statement. The SPARQL result has one row per combination of the optional
multi-valued fields (party, image, family name), so rows are folded per statement.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

from bdf.names import clean_text

# position class (or single position) QID -> government_role.kind
KIND_OF_CLASS = {
    "Q4970706": "kanzler",  # Bundeskanzler
    "Q248352": "minister",  # Bundesminister (class)
    "Q30545012": "minister",  # Chef des Bundeskanzleramtes (in this government a Bundesminister für besondere Aufgaben)
    "Q2325058": "staatsminister",  # Staatsminister (Bund)
    "Q813386": "staatsminister",  # Beauftragter der Bundesregierung für Kultur und Medien (Staatsminister)
    "Q19731005": "parl_sts",  # Parlamentarischer Staatssekretär (class)
    "Q22703996": "beamteter_sts",  # beamteter Staatssekretär (class)
}
KINDS = ("kanzler", "minister", "staatsminister", "parl_sts", "beamteter_sts")


@dataclass
class Role:
    id: str  # Wikidata statement id, "Q85783-…"
    qid: str
    name: str  # the person's German label
    family_name: str | None
    birth_date: str | None
    party: str | None  # short name of the current party ("CDU"), else its label
    image: str | None  # Commons file title, "File:…"
    office: str  # the position's German label
    position_qid: str
    department: str | None
    kind: str
    from_date: str
    to_date: str | None


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def parse_roles(result: dict) -> list[Role]:
    rows: dict[str, list[dict]] = {}
    for b in result["results"]["bindings"]:
        rows.setdefault(_qid(b["st"]["value"]), []).append({k: v["value"] for k, v in b.items()})
    roles = []
    for statement, group in rows.items():
        first = group[0]

        def values(key: str, group: list[dict] = group) -> list[str]:
            return sorted({r[key] for r in group if r.get(key)})

        families = values("familyLabel")
        parties = values("partyShort") or values("partyLabel")
        images = values("image")
        roles.append(
            Role(
                id=statement,
                qid=_qid(first["person"]),
                name=first["personLabel"],
                family_name=families[0] if len(families) == 1 else None,
                birth_date=_date(first.get("birth")),
                party=parties[0] if len(parties) == 1 else None,
                image="File:" + unquote(images[0].rsplit("/", 1)[-1]) if images else None,
                office=first["posLabel"],
                position_qid=_qid(first["pos"]),
                department=first.get("deptLabel"),
                kind=KIND_OF_CLASS[_qid(first["class"])],
                from_date=_date(first["start"]) or "",
                to_date=_date(first.get("end")),
            )
        )
    return sorted(roles, key=lambda r: (KINDS.index(r.kind), r.from_date, r.name))


def split_name(label: str, family_name: str | None) -> tuple[str, str]:
    """(first, last) from the label; the family name (P734) when the label ends with it, else the last word."""
    if family_name and label.endswith(" " + family_name):
        return label[: -len(family_name) - 1], family_name
    first, _, last = label.rpartition(" ")
    return first, last


@dataclass
class CommonsImage:
    title: str
    thumb_url: str
    page_url: str  # the file's description page on Commons
    credit: str  # "<author>, <licence>", e.g. "Steffen Prößdorf, CC BY-SA 4.0"


def _plain(value: str | None) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", "", value or "")))


def parse_commons(pages: list[dict]) -> dict[str, CommonsImage]:
    out = {}
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        if not info.get("thumburl"):
            continue
        meta = info.get("extmetadata") or {}
        author = _plain((meta.get("Artist") or {}).get("value")) or "unbekannt"
        licence = _plain((meta.get("LicenseShortName") or {}).get("value"))
        out[page["title"]] = CommonsImage(
            title=page["title"],
            thumb_url=info["thumburl"],
            page_url=info["descriptionurl"],
            credit=f"{author}, {licence}" if licence else author,
        )
    return out
