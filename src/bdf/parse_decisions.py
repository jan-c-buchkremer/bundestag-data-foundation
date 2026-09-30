"""Rule-based extraction of the decisions the presidency announces in a Plenarprotokoll.

Input is the chair text of a parsed protocol: the ``chair`` paragraphs directly under each
``<tagesordnungspunkt>`` and the ``chair`` paragraphs inside speeches, in document order.
The text is cut into sentences and read as a stream of events:

- a *result sentence* ("… ist damit angenommen", "… abgelehnt bei Zustimmung der AfD") closes a
  unit. With a vote question or a "mit den Stimmen …" clause in the unit it is a show-of-hands
  decision (``handzeichen``), with "Mit Ja haben gestimmt …" it is the announced result of a
  roll-call vote, and on procedure (Überweisung, Tagesordnung, Aufsetzung …) it is skipped;
- an *opening sentence* ("Ich eröffne die namentliche Abstimmung über …") is a roll-call vote
  (``namentlich``); results are announced later, often under another agenda item, and are paired
  with the openings in order (a Drucksache named in both wins).

Fraction positions come from the answers to "Wer stimmt dafür? – Das sind …", "Wer stimmt
dagegen? – …", "Enthaltungen? – …" and from "mit den Stimmen der X gegen die Stimmen der Y bei
Enthaltung der Z". A fraction keeps the first position found for it. See docs/decisions.md for
the measured recall and the phrasings that are not handled.
"""

import json
import re
from dataclasses import dataclass, field

from bdf.names import DRUCKSACHE_RE, normalize_fraction
from bdf.parse_protocol import Protocol

# Fractions of the house and the governing coalition per Wahlperiode, as normalize_fraction spells them.
HOUSE = {21: ("CDU/CSU", "AfD", "SPD", "BÜNDNIS 90/DIE GRÜNEN", "Die Linke")}
COALITION = {21: ("CDU/CSU", "SPD")}

YES, NO, ABSTAIN = "yes", "no", "abstain"

_ABBREV = r"(?<!\bDr\.)(?<!\bNr\.)(?<!\bAbs\.)(?<!\bProf\.)(?<!\bbzw\.)(?<!\bca\.)(?<!\bSt\.)(?<!\bgem\.)(?<!\bvgl\.)"
_SENTENCE_END = re.compile(r"(?<=[.!?:])" + _ABBREV + r"\s+(?=[„\"A-ZÄÖÜ0-9–])")
_DRUCKSACHE_LINE = re.compile(r"^Drucksachen? \d+/\d+")
_CHAIR_NAME = re.compile(r"^(?:Alters)?(?:Vize)?[Pp]räsident(?:in)?\b.*:$")

_RESULT = re.compile(r"\b(angenommen|abgelehnt)\b")
_RESULT_VERB = re.compile(r"\b(?:ist|sind|wurde|wurden|worden|bleibt)\b")
_NOT_RESULT = re.compile(r"\b(?:Zwischenfrage|Kurzintervention|[Nn]achdem|[Ww]enn|[Ff]alls|sollte|würde|Annahme)\b")
# a vote closed without "angenommen": "Dann ist das einstimmig so beschlossen", "Damit ist das Gesetz beschlossen"
_IMPLICIT_RESULT = re.compile(
    r"\beinstimmig\b[^.?]{0,40}\bbeschlossen\b|\b(?:so|damit|dies|dann)\b[^.?]{0,40}\bbeschlossen(?: worden)?\b"
    r"|\bbeschlossen (?:worden )?ist\b"
)
_ROLL_CALL_RESULT = re.compile(r"[Mm]it Ja haben (?:\w+ )?gestimmt|[Aa]uf Ja entfielen|[Mm]it Ja, [^.]*haben gestimmt")
# a vote needing the majority of members (Art. 87 Abs. 3 GG, Einspruch des Bundesrates)
_MAJORITY_RESULT = re.compile(r"hat (?:damit )?die erforderliche Mehrheit (?:von \d+ Stimmen )?(nicht )?erreicht")
# a roll-call vote being called ("… hat namentliche Abstimmung verlangt", "zur nächsten namentlichen Abstimmung")
_CALL = re.compile(
    r"namentliche\w* \w*[Aa]bstimmung\w*[^.]{0,30}(?:verlangt|beantragt)"
    r"|namentliche\w* \w*[Aa]bstimmung(?:en)?:? (?:über|zu)\b"
    r"|zur (?:\w+ )?namentlichen"
)
_OPENING = re.compile(r"\beröffne\b|\beröffnet\b|kann begonnen werden")
_NOT_OPENING = re.compile(
    r"\b(?:Bevor|bevor|Nachdem|nachdem|werde|würde|gleich|möchte|Sitzung|Aussprache|Wahl|Wahlen)\b"
)
_PLURAL_OPENING = re.compile(r"\b(beiden|zwei|drei|vier|fünf|sechs|\d+)? ?namentlichen \w*[Aa]bstimmungen\b")
_NUMBER_WORDS = {"eine": 1, "einer": 1, "keine": 0, "beiden": 2, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
                 "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10}  # fmt: skip

# a question the presidency puts to the house, by the position an answer to it stands for
_QUESTIONS = [
    (YES, r"Wer stimmt (?:dafür|für|zu)\b[^?–]*\?"),
    (YES, r"Wer ist dafür\?"),
    (YES, r"Wer möchte zustimmen\?"),
    (YES, r"\bdie [^.?–]*?zustimmen (?:wollen|möchten)[^.?–]*?(?:Handzeichen|zu erheben)[^.?–]*[.?!]?"),
    (YES, r"Ich bitte um (?:das |Ihr )?Handzeichen[.!]?"),
    (NO, r"Wer stimmt (?:dagegen|gegen)\b[^?–]*\?"),
    (NO, r"Wer ist dagegen\?"),
    (NO, r"(?:Gibt es )?Gegenstimmen\?"),
    (NO, r"Gegenprobe[!.:]?"),
    (NO, r"\bdie [^.?–]*?dagegen ?stimmen (?:wollen|möchten)[^.?–]*[.?!]?"),
    (ABSTAIN, r"Wer enthält sich(?: der Stimme)?\?"),
    (ABSTAIN, r"Wer möchte sich enthalten\?"),
    (ABSTAIN, r"Enthält sich jemand\?"),
    (ABSTAIN, r"(?:Gibt es )?(?:Stimm)?[Ee]nthaltungen\?"),
]
_QUESTION_RE = re.compile("|".join(f"(?P<q{i}>{p})" for i, (_, p) in enumerate(_QUESTIONS)))
_VOTE_EVIDENCE = re.compile(
    _QUESTION_RE.pattern + r"|mit (?:den Stimmen|der Mehrheit|großer Mehrheit)|\beinstimmig\b|Handzeichen"
)

# clauses of a result sentence: "mit den Stimmen der X gegen die Stimmen der Y bei Enthaltung der Z"
_CLAUSE_RE = re.compile(
    r"(?P<majority>\bmit (?:den Stimmen|der Stimme|der Mehrheit|großer Mehrheit|Mehrheit)\b)"
    r"|(?P<against>\bgegen (?:die )?Stimmen?\b)"
    r"|(?P<abstain>\b(?:bei|und) (?:Stimm)?[Ee]nthaltung(?:en)?\b)"
    r"|(?P<approve>\bbei Zustimmung\b)"
    r"|(?P<reject>\bbei (?:Ablehnung|Gegenstimmen)\b)"
)

_FRACTION_RE = re.compile(
    r"(?P<rest>\balle(?:n)? (?:übrigen|anderen)(?: Fraktionen)?|\bRest des Hauses|\bübrigen Fraktionen)"
    r"|(?P<all>\b[Aa]lle Fraktionen|\b[Aa]lle\b(?! Abgeordneten)|\b(?:gesamte|ganze) Haus\b|\beinstimmig\b)"
    r"|(?P<coalition>\bKoalition(?:sfraktionen)?\b)"
    r"|(?P<opposition>\bOpposition(?:sfraktionen)?\b)"
    r"|(?P<gruene>BÜNDNIS(?:SES)? 90/ ?DIE GRÜNEN|Bündnis(?:ses)? 90/ ?Die Grünen|\bGrünen?(?:fraktion)?\b)"
    r"|(?P<linke>\b(?:Die|DIE) (?:Linke|LINKE)\b|\bLinken?(?:fraktion)?\b|\bLinksfraktion\b|\bLinkspartei\b)"
    r"|(?P<union>CDU/CSU|\bUnion(?:sfraktion)?\b)"
    r"|(?P<spd>\bSPD\b)"
    r"|(?P<afd>\bAfD\b)"
    r"|(?P<fraktionslos>\bfraktionslose?n?\b)"
)
_FRACTION_LABEL = {
    "gruene": "Bündnis 90/Die Grünen",
    "linke": "Die Linke",
    "union": "CDU/CSU",
    "spd": "SPD",
    "afd": "AfD",
    "fraktionslos": "fraktionslos",
}
_PARTIAL = re.compile(
    r"(?:Teile?n?|Abgeordnete\w*|Mitglied\w*|einige\w*|einzelne\w*) (?:der|des|von der)(?: Fraktion)? $"
)

_OBJECT = re.compile(
    r"\b(Gesetzentw(?:urf|ürfe)|Beschlussempfehlung(?:en)?|Entschließungsantr(?:ag|äge)|Änderungsantr(?:ag|äge)"
    r"|Sammelübersicht(?:en)?(?: \d+)?|Wahlvorschl(?:ag|äge)|Antr(?:ag|äge)|Entschließung|Verordnung|Vorlage"
    r"|Einzelplan(?: \d+)?|Haushaltsgesetz(?: \d{4})?|Einspruch|Vertrag|Abkommen)\b"
)
_INTRO = re.compile(
    r"Abstimmung:? (?:über|zu) (?:den|die|das|dem|der) (?P<phrase>.+?)"
    r"(?=,? (?:auf (?:der |den )?Drucksachen? |mit dem Titel|in der Ausschussfassung|in zweiter|in dritter)|[.:;(]|$)"
)
_TITLE = re.compile(r"„([^“]{3,})“")
_PROCEDURAL = re.compile(
    r"Überweisung|Aufsetzung|Absetzung|Zurückverweisung|Rücküberweisung|\bTagesordnung\b|Fristverkürzung"
    r"|Herbeirufung|Herbeizitierung|Unterbrechung|Vertagung|Antrag zur Geschäftsordnung|Geschäftsordnungsantrag"
    r"|Zwischenfrage|Sitzungsunterbrechung"
)
_STRONG_REF = re.compile(
    r"(?:^|zurück\w*\b.{0,30}?|\bkomme\w* (?:jetzt |nun |noch mal |noch einmal )?(?:zu[mr]?|zu dem) )"
    r"(Tagesordnungspunkt|Zusatzpunkt|Einzelplan)(?:e|s|en)? (\d+)"
)
_TOP_TOKEN = re.compile(r"(Tagesordnungspunkt\w*|TOP|Zusatzpunkt\w*|ZP|Einzelplan\w*)|(\d+)(?:\s*(?:bis|-|–)\s*(\d+))?")


@dataclass
class Decision:
    sitting_id: str
    agenda_item_id: str | None
    position: int  # order among the decisions of the sitting
    kind: str  # namentlich | handzeichen
    subject: str
    drucksache_number: str | None
    result: str | None  # angenommen | abgelehnt
    text: str
    fractions: dict[str, str] = field(default_factory=dict)  # fraction -> yes | no | abstain
    counts: tuple[int, int, int] | None = None  # announced yes / no / abstain (roll-call votes)
    opened: bool = False  # roll-call vote whose opening was read (else only its result was)
    drucksachen: list[str] = field(default_factory=list)  # every number named, for linking
    n: int = 0
    id: str = ""
    roll_call_vote_id: str | None = None
    sub_item_id: str | None = None
    # where the chair's words were read: agenda item and the agenda_item_paragraph position it stood at
    at_item: str | None = None
    at_paragraph: int | None = None


@dataclass
class _Sentence:
    agenda_item_id: str
    paragraph: int  # running number of the chair paragraph in the sitting
    text: str
    at: int | None = None  # agenda_item_paragraph.position it stands at (a speech: the last one before it)


@dataclass
class _Announcement:
    after: int  # decisions of the sitting found before it
    agenda_item_id: str
    result: str | None
    counts: tuple[int, int, int] | None
    drucksachen: list[str]
    text: str
    subject: str
    at: int | None = None


# --- chair text as a stream of sentences -----------------------------------------------------


def chair_stream(protocol: Protocol) -> list[_Sentence]:
    """Chair sentences of the whole sitting in document order: agenda item paragraphs and
    the chair paragraphs of the speeches, interleaved by speech position."""
    order = {a["id"]: i for i, a in enumerate(protocol.agenda_items)}
    keyed: list[tuple[tuple, str, str, int | None]] = []
    for p in protocol.agenda_paragraphs:
        # the Drucksache lines of the agenda (T_Drs) often stand between the chair's sentences
        if p["kind"] == "chair" or (p["kind"] == "procedural" and _DRUCKSACHE_LINE.match(p["text"])):
            item = p["agenda_item_id"]
            keyed.append(((p["after_speeches"], order[item], 1, p["position"]), item, p["text"], p["position"]))
    for s in protocol.speeches:
        if s.agenda_item_id is None:
            continue
        for i, (kind, text) in enumerate(s.paragraphs):
            if kind == "chair":
                before = [
                    p["position"] for p in protocol.agenda_paragraphs
                    if p["agenda_item_id"] == s.agenda_item_id and p["after_speeches"] <= s.position - 1
                ]  # fmt: skip
                keyed.append(((s.position - 1, order[s.agenda_item_id], 2, i), s.agenda_item_id, text,
                              max(before, default=None)))  # fmt: skip
    keyed.sort(key=lambda k: k[0])
    out: list[_Sentence] = []
    for n, (_, item_id, text, at) in enumerate(keyed, start=1):
        if _CHAIR_NAME.match(text):
            continue
        # footnote markers are glued to the text: "…um 11:10 Uhr sein.2Ergebnis Seite 11140 C"
        text = re.sub(r"(?<=[.:])\d?(?:Ergebnisse?|Anlage|Namensverzeichnis)\b.*$", "", text)
        for sentence in _SENTENCE_END.split(text):
            if sentence.strip():
                out.append(_Sentence(item_id, n, sentence.strip(), at))
    return out


# --- fractions -----------------------------------------------------------------------------


def _mentions(text: str, wp: int) -> list[tuple[str, ...] | str]:
    """Fractions named in a piece of text; 'rest' and 'all' are markers resolved by the caller."""
    out: list = []
    for m in _FRACTION_RE.finditer(text):
        kind = m.lastgroup
        if kind in _FRACTION_LABEL and _PARTIAL.search(text[max(0, m.start() - 40) : m.start()]):
            continue  # "bei Abgeordneten der SPD": part of a fraction, no fraction position
        if kind in ("rest", "all"):
            out.append(kind)
        elif kind == "coalition":
            out.append(COALITION.get(wp, ()))
        elif kind == "opposition":
            out.append(tuple(f for f in HOUSE.get(wp, ()) if f not in COALITION.get(wp, ())))
        else:
            out.append((normalize_fraction(_FRACTION_LABEL[kind]),))
    return out


def _assign(positions: dict[str, str], mentions: list, position: str, wp: int) -> None:
    for m in mentions:
        names = tuple(f for f in HOUSE.get(wp, ()) if f not in positions) if m in ("rest", "all") else m
        for f in names:
            positions.setdefault(f, position)


def fraction_positions(
    text: str, result_sentence: str, result: str | None, wp: int, unanimous: bool = True
) -> dict[str, str]:
    """Positions from the answers to the vote questions in ``text``, then from the result sentence.
    ``unanimous`` lets a bare "einstimmig" stand for every fraction."""
    positions: dict[str, str] = {}
    questions = list(_QUESTION_RE.finditer(text))
    stop = text.find(result_sentence) if result_sentence in text else len(text)
    for i, q in enumerate(questions):
        if q.start() >= stop:
            break
        position = _QUESTIONS[int(q.lastgroup[1:])][0]
        end = questions[i + 1].start() if i + 1 < len(questions) else stop
        _assign(positions, _mentions(text[q.end() : min(end, stop)], wp), position, wp)
    clauses = list(_CLAUSE_RE.finditer(result_sentence))
    majority = YES if result == "angenommen" else NO
    for i, c in enumerate(clauses):
        end = clauses[i + 1].start() if i + 1 < len(clauses) else len(result_sentence)
        position = {
            "majority": majority,
            "against": NO if majority == YES else YES,
            "abstain": ABSTAIN,
            "approve": YES,
            "reject": NO,
        }[c.lastgroup]
        _assign(positions, _mentions(result_sentence[c.end() : end], wp), position, wp)
    if unanimous and not positions and re.search(r"\beinstimmig\b", text):
        _assign(positions, ["all"], majority, wp)
    return positions


# --- subject and Drucksache ------------------------------------------------------------------


def _object(sentence: str) -> str | None:
    m = _OBJECT.search(sentence)
    return m.group(1) if m else None


def _stem(noun: str) -> str:
    return re.sub(r"(?:en|e)$", "", noun.split()[0]).replace("äge", "ag").replace("ürfe", "urf")


def _drucksache_for(text: str, noun: str | None, *, last: bool = False) -> str | None:
    """The Drucksache named after the object noun, before another object noun is named (the first
    such pair in the text, or the last)."""
    if not noun:
        return None
    stem = _stem(noun)
    objects = list(_OBJECT.finditer(text))
    found = None
    for i, o in enumerate(objects):
        if not o.group(1).startswith(stem):
            continue
        end = objects[i + 1].start() if i + 1 < len(objects) else len(text)
        m = DRUCKSACHE_RE.search(text, o.end(), min(end, o.end() + 250))
        if m:
            found = m.group(1)
            if not last:
                return found
    return found


def _subject(vote_text: str, noun: str | None, stage: str, earlier: str | None, fallback: str) -> str:
    """What was voted on: the "Abstimmung über …" phrase of the vote naming its object noun (or one from
    earlier in the agenda item for a bill's later readings), the stage, and a quoted title."""
    stem = _stem(noun) if noun else ""
    phrases = [m.group("phrase") for m in _INTRO.finditer(vote_text) if stem in m.group("phrase")]
    base = phrases[-1] if phrases else earlier or fallback
    title = _TITLE.search(vote_text)
    return _clip(base + stage + (f" „{title.group(1)}“" if title and title.group(1) not in base else ""))


def _clip(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --- agenda item resolution ------------------------------------------------------------------


def _top_keys(top_id: str) -> set[tuple[str, int]]:
    keys, kind = set(), None
    for m in _TOP_TOKEN.finditer(top_id or ""):
        if m.group(1):
            kind = {"T": "Tagesordnungspunkt", "Z": "Zusatzpunkt", "E": "Einzelplan"}[m.group(1)[0]]
        elif kind:
            first = int(m.group(2))
            last = int(m.group(3)) if m.group(3) else first
            keys.update((kind, k) for k in range(first, min(last, first + 50) + 1))
    return keys


class _Items:
    def __init__(self, protocol: Protocol):
        self.items = protocol.agenda_items
        self.keys = {a["id"]: _top_keys(a["top_id"]) for a in self.items}
        self.numbers = {a["id"]: set(re.findall(r"\d+/\d+", a["drucksache_numbers"])) for a in self.items}
        self.order = {a["id"]: i for i, a in enumerate(self.items)}

    def by_ref(self, text: str, current: str) -> str | None:
        # "Damit kehren wir zurück in die Debatte zu Zusatzpunkt 18" returns to a debate, not to a vote
        refs = [
            (m.group(1), int(m.group(2)))
            for s in text
            for m in _STRONG_REF.finditer(s)
            if not re.search(r"Debatte|Aussprache", m.group(0))
        ]
        if not refs:
            return None
        key = refs[-1]
        hits = [a for a, ks in self.keys.items() if key in ks]
        if current in hits:
            return current
        before = [a for a in hits if self.order[a] <= self.order[current]]
        return (before or hits or [None])[-1]

    def by_drucksache(self, numbers: list[str], current: str) -> str | None:
        if any(n in self.numbers[current] for n in numbers):
            return current
        for n in numbers:
            hits = [a for a, ns in self.numbers.items() if n in ns]
            if len(hits) == 1:
                return hits[0]
        return None


# --- extraction ----------------------------------------------------------------------------


def _count(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else _NUMBER_WORDS.get(v.lower())


def _roll_call_result(sentence: str) -> str | None:
    if m := _RESULT.search(sentence):
        return m.group(1) if _RESULT_VERB.search(sentence) else None
    if m := _MAJORITY_RESULT.search(sentence):
        return "abgelehnt" if m.group(1) else "angenommen"
    return None


def _roll_call_counts(text: str) -> tuple[int, int, int] | None:
    yes = _count(r"(?:[Mm]it Ja,? [^.]*?haben (?:\w+ )?gestimmt|[Aa]uf Ja entfielen):? (\d+)", text)
    no = _count(r"(?:[Mm]it Nein(?: haben (?:\w+ )?gestimmt)?|[Aa]uf Nein(?: entfielen)?):? (\d+)", text)
    if yes is None or no is None:
        return None
    abstain = _count(r"Enthaltung(?:en)?(?: gab es)?:? (\d+)\b", text)
    if abstain is None:
        abstain = _count(r"\b(\d+|eine|keine) Enthaltung", text) or 0
    return yes, no, abstain


def _trim(unit: list[_Sentence], start: int) -> list[_Sentence]:
    """The sentences of a vote: from the paragraph of its first question, plus up to two preceding
    paragraphs that introduce it (Drucksache, recommendation, "Dritte Beratung")."""
    para = unit[start].paragraph
    paras = sorted({s.paragraph for s in unit[:start]}, reverse=True)
    keep = {para}
    for p in paras[:2]:
        text = " ".join(s.text for s in unit if s.paragraph == p)
        if re.search(r"Abstimmung|empfiehlt|Drucksache|Beratung|Tagesordnungspunkt|Zusatzpunkt|antrag|entwurf", text):
            keep.add(p)
        else:
            break
    return [s for s in unit if s.paragraph in keep or s.paragraph > para]


def extract(
    protocol: Protocol,
    skipped: list[tuple[str, str]] | None = None,
    pending: list[tuple[Decision, tuple[int, int] | None]] | None = None,
) -> list[Decision]:
    """Every decision announced in the sitting, in order. Roll-call decisions are not linked to
    roll_call_vote yet (see ``link``); their ids and ``n`` are set there.

    ``skipped`` collects the result sentences that did not become a decision, with the reason
    (``procedural``, ``no_vote``, ``conditional``, ``election``), for the quality report. ``pending`` are the
    roll-call decisions of the previous sitting whose result was not read out there, with their vote's final
    yes/no counts:
    a result read out at the start of the next sitting day is theirs when the counts or a Drucksache agree."""
    skipped = [] if skipped is None else skipped
    wp, sid = protocol.wahlperiode, protocol.sitting_id
    items = _Items(protocol)
    decisions: list[Decision] = []
    announcements: list[_Announcement] = []
    intros: dict[str, list[str]] = {}  # "Abstimmung über …" phrases seen per agenda item
    item_text: dict[str, str] = {}  # chair text seen per agenda item
    unit: list[_Sentence] = []
    current, context = None, None

    def resolve(text: list[_Sentence], numbers: list[str]) -> str:
        return (
            items.by_ref([s.text for s in text], current) or items.by_drucksache(numbers, current) or context or current
        )

    stream = chair_stream(protocol)
    i = 0
    while i < len(stream):
        sentence = stream[i]
        i += 1
        if sentence.agenda_item_id != current:
            current, context, unit = sentence.agenda_item_id, None, []
        unit.append(sentence)
        s = sentence.text
        item_text[current] = f"{item_text.get(current, '')} {s}"
        for m in _INTRO.finditer(s):
            intros.setdefault(current, []).append(m.group("phrase"))
        unit_text = " ".join(x.text for x in unit)

        if _ROLL_CALL_RESULT.search(s):
            # the rest of the counts and the result sentence follow, sometimes in the next paragraphs
            taken = 0
            while (
                i < len(stream)
                and taken < 4
                and not _roll_call_result(unit[-1].text)
                and stream[i].agenda_item_id == current
                and stream[i].paragraph <= unit[-1].paragraph + 1
                and not _QUESTION_RE.search(stream[i].text)
                and not _OPENING.search(stream[i].text)
            ):
                unit.append(stream[i])
                i += 1
                taken += 1
            while not _roll_call_result(unit[-1].text) and not _ROLL_CALL_RESULT.search(unit[-1].text):
                i -= 1  # no result sentence: give back what was read past the counts
                unit.pop()
            yes_at = len(unit) - 1 - next(k for k, x in enumerate(reversed(unit)) if _ROLL_CALL_RESULT.search(x.text))
            # from the sentence that introduces the result ("… ermittelte Ergebnis der namentlichen Abstimmung …")
            start = next((k for k in range(yes_at, -1, -1) if re.search(r"Ergebnis|namentlich", unit[k].text)), yes_at)
            text = " ".join(x.text for x in unit[start:])
            found = _roll_call_result(unit[-1].text)
            around = " ".join(
                [x.text for x in unit[max(0, start - 2) : start]] + [text] + [x.text for x in stream[i : i + 2]]
            )
            unit = []
            if "namentlich" not in text and re.search(r"\bWahl|gewählt|erforderliche Mehrheit", around):
                skipped.append(("election", text))
                continue
            announcements.append(
                _Announcement(
                    len(decisions),
                    current,
                    found,
                    _roll_call_counts(text),
                    DRUCKSACHE_RE.findall(text),
                    text,
                    _object(unit_text.split(" Mit Ja")[0][-300:]) or "",
                    sentence.at,
                )
            )
            continue

        if (
            _OPENING.search(s)
            and re.search(r"[Aa]bstimmung", s)
            and not _NOT_OPENING.search(s)
            and ("namentlich" in unit_text or "Haushaltsgesetz" in s)
        ):
            noun = _object(s)
            numbers = DRUCKSACHE_RE.findall(s)
            if not (noun or numbers or _CALL.search(unit_text)):
                # "…, und die namentliche Abstimmung ist eröffnet": a vote opened a moment ago, said again
                unit = []
                continue
            vote_text = " ".join(x.text for x in _trim(unit, len(unit) - 1))
            if not numbers and noun:
                found = _drucksache_for(item_text[current], noun, last=True)
                numbers = [found] if found else []
            numbers = numbers or DRUCKSACHE_RE.findall(vote_text)[-1:]
            plural = _PLURAL_OPENING.search(s)
            count = 1
            if plural:
                word = plural.group(1)
                count = (int(word) if word.isdigit() else _NUMBER_WORDS[word]) if word else max(2, len(numbers))
            item = resolve(unit, numbers)
            context = item
            phrases = re.findall(r"über (?:den|die|das) (.+?)(?=,? auf (?:der |den )?Drucksache|[.;]|$)", vote_text)
            noun = noun or _object(vote_text[-300:])
            stage = ""
            if "Schlussabstimmung" in s or "Schlussabstimmung" in unit_text[-400:]:
                stage = " – Schlussabstimmung"
            elif "zweite Beratung" in s or "zweiten Beratung" in s:
                stage = " – zweite Beratung"
            subject = _subject(
                vote_text,
                noun,
                stage,
                _last_intro(intros, current, noun) if noun else None,
                phrases[-1] if phrases else "Namentliche Abstimmung",
            )
            for k in range(count):
                decisions.append(
                    Decision(
                        sitting_id=sid, agenda_item_id=item, position=len(decisions) + 1, kind="namentlich",
                        subject=subject, drucksache_number=numbers[k] if k < len(numbers) else
                        (numbers[0] if numbers else None), result=None, text=vote_text,
                        drucksachen=list(numbers), opened=True, at_item=current, at_paragraph=sentence.at,
                    )
                )  # fmt: skip
            unit = []
            continue

        implicit = False
        if not (_RESULT.search(s) and _RESULT_VERB.search(s)):
            if not _IMPLICIT_RESULT.search(s):
                continue
            implicit = True
        if _NOT_RESULT.search(s):
            skipped.append(("conditional", s))
            continue
        result = "angenommen" if implicit else _RESULT.search(s).group(1)
        if implicit:  # only after a vote put to the house, with no decision read for it yet
            first_q = next((i for i, x in enumerate(unit[:-1]) if _QUESTION_RE.search(x.text)), None)
        else:
            first_q = next((i for i, x in enumerate(unit) if _VOTE_EVIDENCE.search(x.text)), None)
        if first_q is None:  # no vote was put: narrative ("…, der abgelehnt worden ist")
            if not implicit:
                skipped.append(("no_vote", s))
                unit = []
            continue
        vote = _trim(unit, first_q)
        vote_text = " ".join(x.text for x in vote)
        question_sentence = unit[first_q].text
        preceding = unit[first_q - 1].text if first_q > 0 else ""
        if implicit and (_PROCEDURAL.search(s)):
            continue  # "Dann ist die Überweisung so beschlossen": leaves the passage as it was
        unit = []
        if (
            _PROCEDURAL.search(s)
            or _PROCEDURAL.search(question_sentence)
            or (_PROCEDURAL.search(preceding) and not _OBJECT.search(question_sentence))
        ):
            skipped.append(("procedural", s))
            continue
        noun = _object(s) or _object(question_sentence)
        stage = ""
        if "zweiter Beratung" in s:
            stage = " – zweite Beratung"
        elif re.search(r"Schlussabstimmung|dritter Beratung", vote_text):
            stage = " – Schlussabstimmung"
        # a bill's second and third reading follow its introduction, often in an earlier unit
        follow_up = bool(stage) or (noun or "").startswith("Gesetzentw")
        subject = _subject(
            vote_text, noun, stage, _last_intro(intros, current, noun) if follow_up else None, noun or "Vorlage"
        )
        drucksache = _drucksache_for(vote_text, noun)
        if drucksache is None and follow_up:
            drucksache = _drucksache_for(item_text.get(current, ""), noun, last=True)
        if drucksache is None:
            m = DRUCKSACHE_RE.search(vote_text)
            drucksache = m.group(1) if m else None
        numbers = DRUCKSACHE_RE.findall(vote_text)
        item = resolve(vote, ([drucksache] if drucksache else []) + numbers)
        context = item
        decisions.append(
            Decision(
                sitting_id=sid, agenda_item_id=item, position=len(decisions) + 1, kind="handzeichen",
                subject=subject, drucksache_number=drucksache, result=result, text=vote_text,
                fractions=fraction_positions(vote_text, s, result, wp, unanimous=not implicit), drucksachen=numbers,
                at_item=current, at_paragraph=sentence.at,
            )
        )  # fmt: skip

    _pair_announcements(decisions, announcements, sid, pending or [])
    _attach_sub_items(decisions, protocol)
    n = 0
    for d in decisions:
        if d.kind == "handzeichen":
            n += 1
            d.n, d.id = n, f"{sid}/h{n}"
    return decisions


_SAMMEL = re.compile(r"Sammelübersicht (\d+)")


def _attach_sub_items(decisions: list[Decision], protocol: Protocol) -> None:
    """Set ``sub_item_id``: the sub-item the chair had last called up where the decision was read, if the
    decision stayed with that agenda item. A sub-item names what the chair's words leave out: the number
    of a Sammelübersicht ("Auch diese Sammelübersicht ist einstimmig angenommen") and a lone Drucksache."""
    subs: dict[str, list[dict]] = {}
    for sub in protocol.agenda_sub_items:
        subs.setdefault(sub["agenda_item_id"], []).append(sub)
    for d in decisions:
        if d.at_item != d.agenda_item_id or d.at_paragraph is None:
            continue
        sub = next((x for x in reversed(subs.get(d.at_item, [])) if x["first_paragraph"] <= d.at_paragraph), None)
        if sub is None:
            continue
        d.sub_item_id = sub["id"]
        numbers = json.loads(sub["drucksache_numbers"])
        if d.drucksache_number is None and len(numbers) == 1:
            d.drucksache_number = numbers[0]
        numbers = set(_SAMMEL.findall(sub["title"] or ""))
        if len(numbers) == 1:
            # the chair's words name none ("Sammelübersicht", "Vorlage") or run on into the next sentence
            named = _SAMMEL.match(d.subject)
            if d.subject == "Vorlage" or (
                d.subject.startswith("Sammelübersicht") and (not named or named[1] in numbers)
            ):
                d.subject = f"Sammelübersicht {next(iter(numbers))}"


def _last_intro(intros: dict[str, list[str]], item: str | None, noun: str | None) -> str | None:
    """The latest "Abstimmung über …" phrase of the agenda item that names the object noun."""
    for phrase in reversed(intros.get(item, [])):
        if noun is None or _stem(noun) in phrase:
            return phrase
    return None


def _pair_announcements(
    decisions: list[Decision],
    announcements: list[_Announcement],
    sid: str,
    pending: list[tuple[Decision, tuple[int, int] | None]],
) -> None:
    """Give each opened roll-call vote the result announced for it.

    An announcement goes to a vote of the previous sitting still waiting for its result when the counts
    (within a few votes: the announcement is provisional) or a Drucksache agree; else to the earliest vote
    opened before it in this sitting that names one of its Drucksachen, else to the earliest one opened
    before it. An announcement with nothing to pair (the opening is worded in a way the parser does not
    know) becomes a decision of its own."""
    for a in announcements:
        earlier = [
            d for d, counts in pending
            if d.counts is None and (
                set(d.drucksachen) & set(a.drucksachen)
                or (counts and a.counts and abs(counts[0] - a.counts[0]) + abs(counts[1] - a.counts[1]) <= 25)
            )
        ]  # fmt: skip
        waiting = [d for d in decisions[: a.after] if d.kind == "namentlich" and d.result is None and d.counts is None]
        match = (
            (earlier[0] if earlier else None)
            or next((d for d in waiting if set(d.drucksachen) & set(a.drucksachen)), None)
            or (waiting[0] if waiting else None)
        )
        if match is None:
            decisions.append(
                Decision(
                    sitting_id=sid, agenda_item_id=a.agenda_item_id, position=len(decisions) + 1,
                    kind="namentlich", subject=a.subject or "Namentliche Abstimmung",
                    drucksache_number=a.drucksachen[0] if a.drucksachen else None, result=a.result,
                    text=a.text, counts=a.counts, drucksachen=a.drucksachen,
                    at_item=a.agenda_item_id, at_paragraph=a.at,
                )
            )  # fmt: skip
            continue
        match.result, match.counts = a.result or match.result, a.counts
        match.text = f"{match.text}\n\n{a.text}"
        if match.drucksache_number is None and a.drucksachen:
            match.drucksache_number = a.drucksachen[0]


# --- roll-call link --------------------------------------------------------------------------


def link(decisions: list[Decision], votes: list[dict]) -> None:
    """Set id, n and roll_call_vote_id of the roll-call decisions of one sitting.

    ``votes`` are the sitting's roll_call_vote rows (id, number, yes, no, drucksache_number).
    Pairing: announced yes/no counts close to a vote's (the announcement is the provisional
    result, the XLSX the final one), then a shared Drucksache, then the remaining ones in order
    when as many decisions as votes are left. A decision without a vote gets the id
    ``<sitting>/n<k>``; with one, the vote's id.
    """
    roll_call = [d for d in decisions if d.kind == "namentlich"]
    free = {v["id"]: v for v in sorted(votes, key=lambda v: v["number"])}
    pairs = sorted(
        (abs(d.counts[0] - v["yes"]) + abs(d.counts[1] - v["no"]), i, v["id"])
        for i, d in enumerate(roll_call)
        if d.counts
        for v in votes
    )
    for dist, i, vid in pairs:
        if dist <= 25 and roll_call[i].roll_call_vote_id is None and vid in free:
            roll_call[i].roll_call_vote_id = vid
            del free[vid]
    for d in roll_call:
        if d.roll_call_vote_id is None:
            hit = [
                vid for vid, v in free.items()
                if set(d.drucksachen) & set(DRUCKSACHE_RE.findall(v["drucksache_number"] or ""))
            ]  # fmt: skip
            if len(hit) == 1:
                d.roll_call_vote_id = hit[0]
                del free[hit[0]]
    rest = [d for d in roll_call if d.roll_call_vote_id is None]
    if rest and len(rest) == len(free):
        for d, vid in zip(rest, list(free), strict=True):
            d.roll_call_vote_id = vid
    by_id = {v["id"]: v for v in votes}
    for k, d in enumerate(roll_call, start=1):
        d.n = k
        d.id = d.roll_call_vote_id or f"{d.sitting_id}/n{k}"
        vote = by_id.get(d.roll_call_vote_id)
        if d.result is None and vote and vote["yes"] != vote["no"]:
            # result not read out in the protocol (last vote of the day, printed only): simple majority of the XLSX
            d.result = "angenommen" if vote["yes"] > vote["no"] else "abgelehnt"
