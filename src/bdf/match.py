"""Match names from votes, DIP and abgeordnetenwatch to person rows (MdB ids)."""

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from bdf.names import normalize_name

# Known spelling differences between sources and the Stammdaten: (source last, source first),
# both normalised, -> MdB id. Extend when `ingest` reports an unmatched name that is a real MdB.
ALIASES: dict[tuple[str, str], str] = {}

_PREFIX_RE = re.compile(r"^((von|van|de|del|dos|da|di|zu|der|la|freiherr|graf) )+")


@dataclass
class Candidate:
    id: str
    birth_year: str | None


def _first_token(normalized_first: str) -> str:
    return normalized_first.split(" ", 1)[0]


class _NameIndex:
    def __init__(self, rows: list[sqlite3.Row]):
        self.by_full: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
        self.by_first_token: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
        for r in rows:
            c = Candidate(r["id"], (r["birth_date"] or "")[:4] or None)
            last, first = normalize_name(r["last_name"]), normalize_name(r["first_name"])
            self.by_full[(last, first)].append(c)
            self.by_first_token[(last, _first_token(first))].append(c)

    def lookup(self, last: str, first: str, birth_year: str | None) -> list[Candidate] | None:
        """First non-empty candidate list over the key variants (normalised input), or None."""
        keys = [(self.by_full, (last, first)), (self.by_first_token, (last, _first_token(first)))]
        if " " in last:  # "labitzke rathert" -> "rathert"
            keys.append((self.by_first_token, (last.split()[-1], _first_token(first))))
        for index, key in keys:
            candidates = index.get(key, [])
            if birth_year and len(candidates) > 1:
                candidates = [c for c in candidates if c.birth_year == birth_year]
            if candidates:
                return candidates
        return None


class PersonIndex:
    """Name lookup: MdBs of one Wahlperiode first, every known person as a fallback
    (Nachrücker who joined after the Stammdaten file was generated have no mandate row yet)."""

    def __init__(self, conn: sqlite3.Connection, wahlperiode: int):
        columns = "p.id, p.last_name, p.first_name, p.birth_date"
        self.tiers = [
            _NameIndex(
                conn.execute(
                    f"SELECT {columns} FROM person p JOIN mandate m ON m.person_id = p.id WHERE m.wahlperiode = ?",
                    (wahlperiode,),
                ).fetchall()
            ),
            _NameIndex(conn.execute(f"SELECT {columns} FROM person p").fetchall()),
        ]

    def match(self, last: str, first: str, birth_year: str | None = None) -> str | None:
        """Return the single matching MdB id or None (ambiguous or unknown)."""
        last = re.sub(r"\s*\(.*?\)", "", last)  # "Mayer (Altötting)" -> "Mayer"
        nlast, nfirst = normalize_name(last), normalize_name(first)
        alias = ALIASES.get((nlast, nfirst))
        if alias:
            return alias
        nlast = _PREFIX_RE.sub("", nlast)
        for tier in self.tiers:
            candidates = tier.lookup(nlast, nfirst, birth_year)
            if candidates is not None:
                return candidates[0].id if len(candidates) == 1 else None
        return None
