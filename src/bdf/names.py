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


# The fraction labels of WP 21, and the groups a speech can count for besides them (speech.speaker_group,
# docs/design.md "Fraction and speaker group").
FRACTIONS = ("AfD", "CDU/CSU", "BÜNDNIS 90/DIE GRÜNEN", "SPD", "Die Linke", "fraktionslos")
NO_FRACTION = "fraktionslos"
GOVERNMENT = "Bundesregierung"
BUNDESRAT = "Bundesrat"  # members of a Land government, who speak for the Bundesrat
OTHER = "Sonstige"  # the Wehrbeauftragte, guests

# Party names as the Stammdaten (person.party) and the Bundeswahlleiterin print them -> the fraction they sit in.
# Parties without a fraction of their own (SSW, the historic ones) map to nothing.
_PARTY_FRACTION = {
    "cdu": "CDU/CSU",
    "csu": "CDU/CSU",
    "spd": "SPD",
    "afd": "AfD",
    "grüne": "BÜNDNIS 90/DIE GRÜNEN",
    "bündnis 90/die grünen": "BÜNDNIS 90/DIE GRÜNEN",
    "die linke": "Die Linke",
    "die linke.": "Die Linke",
}

_LAND_OFFICE = re.compile(r"^(Minister|Ministerin|Ministerpräsident|Ministerpräsidentin|Staatsminister|"
                          r"Staatsministerin|Senator|Senatorin|(Erste[r]? |Regierende[r]? )?Bürgermeister(in)?)"
                          r"\b.*\([^)]+\)$")  # fmt: skip
_GOVERNMENT_COMMISSIONER = re.compile(r"^Beauftragte[r]? der Bundesregierung\b")


def party_fraction(party: str | None) -> str | None:
    """ "CSU" -> "CDU/CSU", "GRÜNE" -> "BÜNDNIS 90/DIE GRÜNEN", "DIE LINKE." -> "Die Linke"; None otherwise."""
    return _PARTY_FRACTION.get(clean_text(party).lower()) if party else None


def originator_group(title: str) -> str | None:
    """The group behind a DIP Urheber title: a fraction ("Fraktion der SPD", "Fraktion DIE LINKE", "Gruppe BSW"),
    the Bundesregierung (also a single ministry, "Bundesministerium der Finanzen"), or None (a committee, the
    Bundesrat, the President)."""
    t = clean_text(title)
    if t.startswith(GOVERNMENT) or t.startswith("Bundesministerium"):
        return GOVERNMENT
    m = re.match(r"^(?:Fraktion|Gruppe)(?:en)?\s+(?:der\s+)?(.+)$", t)
    if not m:
        return None
    fraction = normalize_fraction(m.group(1))
    return fraction if fraction in _FRACTIONS.values() else None


def speaker_group(role: str | None, fraction: str | None, is_government_role: bool) -> str:
    """Who a speech counts for: the Bundesregierung when given in a federal government office (the member's
    fraction does not count then), the Bundesrat for a Land office, else the printed fraction, else Sonstige.
    `is_government_role` is government.parse_role(role) is not None, passed in to keep this module free of it."""
    if role and (is_government_role or _GOVERNMENT_COMMISSIONER.match(role)):
        return GOVERNMENT
    if role and _LAND_OFFICE.match(role):
        return BUNDESRAT
    return normalize_fraction(fraction) or OTHER


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
