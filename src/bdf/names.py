"""Normalisation shared by the three sources: text, dates, fraction labels, person names."""

import re
import unicodedata

# A Drucksache number in free text ("Drucksache 21/7300", vote titles, agenda lines).
DRUCKSACHE_RE = re.compile(r"\b(\d{1,2}/\d{1,6})\b")

# Vote values as stored in individual_vote.vote and as columns of roll_call_vote.
VOTE_VALUES = ("yes", "no", "abstain", "invalid", "absent")

# Canonical fraction labels for the 21st Wahlperiode; keys are lower-cased, whitespace-collapsed
# spellings seen in the Stammdaten (INS_LANG), the protocol XML (<fraktion>) and the vote XLSX.
_FRACTIONS = {
    "cdu/csu": "CDU/CSU",
    "fraktion der christlich demokratischen union/christlich - sozialen union": "CDU/CSU",
    "spd": "SPD",
    "fraktion der sozialdemokratischen partei deutschlands": "SPD",
    "afd": "AfD",
    "fraktion alternative für deutschland": "AfD",
    "bündnis 90/die grünen": "BÜNDNIS 90/DIE GRÜNEN",
    "fraktion bündnis 90/die grünen": "BÜNDNIS 90/DIE GRÜNEN",
    "bü90/gr": "BÜNDNIS 90/DIE GRÜNEN",
    "die linke": "Die Linke",
    "fraktion die linke": "Die Linke",
    "die linke.": "Die Linke",
    "fraktion die linke.": "Die Linke",
    "gruppe die linke": "Die Linke",
    "fdp": "FDP",
    "fraktion der freien demokratischen partei": "FDP",
    "bsw": "BSW",
    "gruppe bsw - bündnis sahra wagenknecht - vernunft und gerechtigkeit": "BSW",
    "fraktionslos": "fraktionslos",
}


def clean_text(s: str | None) -> str:
    """Collapse whitespace; drop the NBSP variants and soft hyphens bundestag.de likes to use."""
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace(" ", " ").replace("­", "")
    return re.sub(r"\s+", " ", s).strip()


def iso_date(d: str | None) -> str | None:
    """'25.03.2025' -> '2025-03-25'; empty -> None."""
    if not d or not d.strip():
        return None
    day, month, year = d.strip().split(".")
    return f"{year}-{month}-{day}"


def normalize_fraction(raw: str | None) -> str | None:
    if not raw:
        return None
    key = clean_text(raw).lower()
    return _FRACTIONS.get(key, raw.strip())


def normalize_name(s: str | None) -> str:
    """Lower-case ASCII form for matching: strips accents, titles, punctuation."""
    if not s:
        return ""
    s = s.lower()
    for umlaut, ascii_ in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("ı", "i")):
        s = s.replace(umlaut, ascii_)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(dr|prof|med|jur|rer|nat|phil|h\.c|dipl|ing|mdb)\b\.?", " ", s)
    s = re.sub(r"[^a-z ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
