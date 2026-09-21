"""Parse MDB_STAMMDATEN.XML into person / mandate / membership rows."""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from bdf.names import iso_date, normalize_fraction
from bdf.raw import RawMeta

_KIND = {"Fraktion/Gruppe": "fraction", "Ausschuss": "committee", "Unterausschuss": "committee"}


def parse(path: Path, meta: RawMeta) -> dict[str, list[dict]]:
    root = ET.parse(path).getroot()
    version = datetime.fromtimestamp(int(root.findtext("VERSION")), tz=UTC).date().isoformat()
    prov = meta.provenance(f"MDB_STAMMDATEN {version}")
    persons, mandates, memberships = [], [], []
    for mdb in root.findall("MDB"):
        pid = mdb.findtext("ID")
        names = mdb.findall("NAMEN/NAME")
        name = next((n for n in names if not n.findtext("HISTORIE_BIS")), names[-1])
        bio = mdb.find("BIOGRAFISCHE_ANGABEN")
        prefix = " ".join(p for p in (name.findtext("ADEL"), name.findtext("PRAEFIX")) if p)
        persons.append(
            {
                "id": pid,
                "first_name": name.findtext("VORNAME") or "",
                "last_name": name.findtext("NACHNAME") or "",
                "name_prefix": prefix or None,
                "academic_title": name.findtext("AKAD_TITEL") or None,
                "birth_date": iso_date(bio.findtext("GEBURTSDATUM")),
                "birth_place": bio.findtext("GEBURTSORT") or None,
                "gender": bio.findtext("GESCHLECHT") or None,
                "party": bio.findtext("PARTEI_KURZ") or None,
                "is_mdb": 1,
                "role": None,
                **prov,
            }
        )
        for wp in mdb.findall("WAHLPERIODEN/WAHLPERIODE"):
            wp_no = int(wp.findtext("WP"))
            mandates.append(
                {
                    "id": f"{pid}/{wp_no}",
                    "person_id": pid,
                    "wahlperiode": wp_no,
                    "from_date": iso_date(wp.findtext("MDBWP_VON")),
                    "to_date": iso_date(wp.findtext("MDBWP_BIS")),
                    "mandate_type": wp.findtext("MANDATSART") or None,
                    "constituency_number": int(wp.findtext("WKR_NUMMER") or 0) or None,
                    "constituency_name": wp.findtext("WKR_NAME") or None,
                    "state": wp.findtext("WKR_LAND") or wp.findtext("LISTE") or None,
                    **prov,
                }
            )
            for n, inst in enumerate(wp.findall("INSTITUTIONEN/INSTITUTION"), start=1):
                kind = _KIND.get(inst.findtext("INSART_LANG") or "", "other")
                name_ = inst.findtext("INS_LANG") or ""
                memberships.append(
                    {
                        "id": f"{pid}/{wp_no}/{n}",
                        "person_id": pid,
                        "wahlperiode": wp_no,
                        "kind": kind,
                        "name": normalize_fraction(name_) if kind == "fraction" else name_,
                        "role": inst.findtext("FKT_LANG") or None,
                        "from_date": iso_date(inst.findtext("MDBINS_VON")),
                        "to_date": iso_date(inst.findtext("MDBINS_BIS")),
                        **prov,
                    }
                )
    return {"person": persons, "mandate": mandates, "membership": memberships}
