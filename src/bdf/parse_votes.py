"""Parse one roll-call vote XLSX from bundestag.de (one row per MdB)."""

from dataclasses import dataclass
from pathlib import Path

import openpyxl

from bdf.names import normalize_fraction

_RESULT_COLUMNS = (
    ("ja", "yes"),
    ("nein", "no"),
    ("enthaltung", "abstain"),
    ("ungültig", "invalid"),
    ("nichtabgegeben", "absent"),
)


@dataclass(frozen=True)
class VoteRow:
    last_name: str
    first_name: str
    fraction: str
    vote: str  # yes | no | abstain | invalid | absent


@dataclass(frozen=True)
class RollCall:
    wahlperiode: int
    sitting: int
    number: int
    rows: list[VoteRow]

    @property
    def id(self) -> str:
        return f"{self.wahlperiode}/{self.sitting}/{self.number}"


def parse(path: Path) -> RollCall:
    sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    header = [str(h).strip().lower() if h is not None else "" for h in next(rows_iter)]
    col = {name: i for i, name in enumerate(header) if name}
    rows: list[VoteRow] = []
    wp = sitting = number = None
    for values in rows_iter:
        if values[col["name"]] is None:
            continue
        wp, sitting, number = (int(values[col[c]]) for c in ("wahlperiode", "sitzungnr", "abstimmnr"))
        vote = next(v for c, v in _RESULT_COLUMNS if int(values[col[c]] or 0) == 1)
        rows.append(
            VoteRow(
                last_name=str(values[col["name"]]).strip(),
                first_name=str(values[col["vorname"]] or "").strip(),
                fraction=normalize_fraction(str(values[col["fraktion/gruppe"]])) or "",
                vote=vote,
            )
        )
    if wp is None:
        raise ValueError(f"no vote rows in {path}")
    return RollCall(wahlperiode=wp, sitting=sitting, number=number, rows=rows)
