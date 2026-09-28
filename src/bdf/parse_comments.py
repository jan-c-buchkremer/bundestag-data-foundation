"""Structure the bracketed comment paragraphs of a Plenarprotokoll: who applauded, heckled, laughed.

A comment paragraph holds one or more parts separated by " – ":

    (Beifall bei der SPD sowie bei Abgeordneten der CDU/CSU – Zuruf von der AfD: Falsch! –
     Stephan Brandner [AfD]: Reden Sie mal Klartext!)

Each part has a kind (Beifall, Zuruf, Gegenruf, Lachen, Heiterkeit, Widerspruch, Zustimmung, Unruhe) and
one or more actors: a whole fraction ("bei der SPD"), some of its members ("bei Abgeordneten der CDU/CSU"),
a named member ("des Abg. Tarek Al-Wazir [BÜNDNIS 90/DIE GRÜNEN]" or "Name [Fraktion]: words"), or the
whole house. A named interjection ("Name [Fraktion]: words") is a Zuruf with the words as text.

The words inside a part may themselves contain " – ", so a part only ends where the next one starts
with a recognised opening.
"""

import re
from dataclasses import dataclass

from bdf.names import clean_text, normalize_fraction

KINDS = {
    "beifall": "beifall",
    "zuruf": "zuruf",
    "zurufe": "zuruf",
    "gegenruf": "gegenruf",
    "gegenrufe": "gegenruf",
    "lachen": "lachen",
    "heiterkeit": "heiterkeit",
    "widerspruch": "widerspruch",
    "zustimmung": "zustimmung",
    "unruhe": "unruhe",
}
# "Anhaltender Beifall", "Lebhafte Heiterkeit", "Weitere Zurufe": the adjective is kept in raw only
_ADJ = (
    r"(?:(?:Weiterer?|Erneuter?|Anhaltender|Lang\s?anhaltender|Lebhafter?|Starker|Vereinzelter?|Große|Allgemeine)\s+)?"
)
_KIND = _ADJ + r"(?:Beifall|Zurufe?|Gegenrufe?|Lachen|Heiterkeit|Widerspruch|Zustimmung|Unruhe)"
# "Name [Fraktion]" of a member, "Name [Ort] [Fraktion]" for namesakes; names never contain brackets or colons
_NAMED = r"[^\[\]:–()]{2,90}?(?:\s\[[^\]]{2,40}\])?\s\[[^\]]{2,60}\]"
# "Sara Nanni [BÜNDNIS 90/DIE GRÜNEN], an die AfD gewandt: …"
_ADDRESSED = r"(?:,\s[^:\[\]]{2,60})?"
_PART_START = re.compile(rf"\s[–-]\s(?=(?:{_KIND}\b|{_NAMED}{_ADDRESSED}\s*:))")

# fraction spellings inside comments, longest first; declined forms of the Greens and the Left
_FRACTION_WORDS = [
    (r"BÜNDNIS(?:SES)?\s90/DIE\s+GRÜNEN?", "BÜNDNIS 90/DIE GRÜNEN"),
    (r"CDU/CSU", "CDU/CSU"),
    (r"SPD", "SPD"),
    (r"AfD", "AfD"),
    (r"(?i:(?:Fraktion\s+)?Die\s+Linke|Linken?)\b", "Die Linke"),
]
_FRACTION_RE = re.compile("|".join(f"(?P<f{i}>{p})" for i, (p, _) in enumerate(_FRACTION_WORDS)))
# named members inside a group: "der Abg. A [SPD] und B [BÜNDNIS 90/DIE GRÜNEN]", "des Abg. C [CDU/CSU]"
_PERSON_RE = re.compile(
    r"(?:Abg\.|,|\bund\b|\bsowie\b)\s+(?P<name>(?:(?!\b(?:und|sowie|bei|der|des)\s)[^\[\],])+?"
    r"(?:\s\[[^\]]{2,40}\])?\s\[[^\]]{2,60}\])"
)
# "Gegenruf des Abg. A [AfD] an den Abg. B [SPD]": B is spoken to, not speaking
_ADDRESSEE_RE = re.compile(rf"\ban\s+(?:den\s+|die\s+)?Abg\.\s+(?P<name>{_NAMED})")
_NAMED_RE = re.compile(rf"^(?P<name>{_NAMED}){_ADDRESSED}\s*:\s*(?P<text>.*)$", re.S)
_NAME_FRACTION = re.compile(r"^(?P<name>.*?)(?:\s\[(?P<place>[^\]]+)\])?\s\[(?P<fraction>[^\]]+)\]$")


@dataclass
class Actor:
    kind: str  # fraction | members | person | house | unknown
    fraction: str | None = None
    name: str | None = None  # persons: the printed name without the bracket


@dataclass
class Part:
    kind: str  # one of KINDS' values, or "other"
    actors: list[Actor]
    text: str | None  # the words of a Zuruf / Gegenruf, when printed
    raw: str
    to: Actor | None = None  # addressee named in the part ("… an den Abg. X [SPD]"), else the speaker is meant


def split_parts(comment: str) -> list[str]:
    body = clean_text(comment)
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    return [p.strip() for p in _PART_START.split(body) if p.strip()]


def _fractions_in(s: str) -> list[str]:
    out = []
    for m in _FRACTION_RE.finditer(s):
        label = next(_FRACTION_WORDS[int(k[1:])][1] for k, v in m.groupdict().items() if v)
        if label not in out:
            out.append(label)
    return out


CANONICAL = {"CDU/CSU", "SPD", "AfD", "BÜNDNIS 90/DIE GRÜNEN", "Die Linke", "fraktionslos"}


def _bracket_fraction(raw: str) -> str | None:
    """The fraction in "Name [Fraktion]"; the protocol has typos there ("BÜNDNIS 90/DIE GÜNEN")."""
    label = normalize_fraction(raw.strip())
    if label in CANONICAL:
        return label
    return "BÜNDNIS 90/DIE GRÜNEN" if label and label.upper().startswith("BÜNDNIS") else None


def _person(named: str) -> Actor:
    m = _NAME_FRACTION.match(named.strip())
    if not m:
        return Actor("unknown", name=named.strip())
    return Actor("person", _bracket_fraction(m["fraction"]), re.sub(r"^Abg\.\s+", "", m["name"].strip()))


def _group_actors(s: str) -> list[Actor]:
    """Actors of "bei der SPD sowie bei Abgeordneten der CDU/CSU und des BÜNDNISSES 90/DIE GRÜNEN"."""
    if re.search(r"\b(ganzen|im)\s+Hause?\b", s) and not _fractions_in(s):
        return [Actor("house")]
    actors = [_person(m["name"]) for m in _PERSON_RE.finditer(s)]
    s = _PERSON_RE.sub(" ", s)
    # "bei der SPD und bei Abgeordneten der CDU/CSU": each "Abgeordneten" clause covers the fractions after it
    # up to the next "bei"/"sowie"/"von"
    for clause in re.split(r"\b(?:sowie|bei|beim|von|vom)\b", s):
        members = re.search(r"\bAbgeordneten\b", clause) is not None
        actors += [Actor("members" if members else "fraction", f) for f in _fractions_in(clause)]
    return actors or [Actor("unknown")]


def parse_part(part: str) -> list[Part]:
    """Usually one Part; two for "Heiterkeit und Beifall bei …", which share their actors."""
    m = _NAMED_RE.match(part)
    if m and not re.match(_KIND, part):
        return [Part("zuruf", [_person(m["name"])], m["text"].strip() or None, part)]
    km = re.match(rf"^({_KIND})(?:\s+und\s+({_KIND}))?\b\s*(.*)$", part, re.S)
    if not km:
        return [Part("other", [Actor("unknown")], None, part)]
    kinds = [KINDS[k.split()[-1].lower()] for k in km.group(1, 2) if k]  # "Lebhafter Beifall" -> beifall
    rest = km.group(3)
    to = None
    if am := _ADDRESSEE_RE.search(rest):
        to, rest = _person(am["name"]), rest[: am.start()] + rest[am.end() :]
    text = None
    if ":" in rest:  # "Zuruf von der AfD: Falsch!" / "Zuruf des Abg. X [AfD]: Falsch!"
        head, _, words = rest.partition(":")
        if "[" not in words.split(" ")[0]:  # a colon inside the bracketed name would be odd; keep simple
            rest, text = head, words.strip() or None
    actors = _group_actors(rest)
    return [Part(kind, actors, text, part, to) for kind in kinds]


def parse(comment: str) -> list[Part]:
    return [p for raw in split_parts(comment) for p in parse_part(raw)]
