"""The spoken questions of a Befragung der Bundesregierung: which turn is a member of the government's opening
statement, a question, an answer, the asker's Nachfrage or another member's Zusatzfrage, and which belong together
(the question_turn table, docs/design.md "Question turns").

Every turn of a Befragung is a ``Speech`` of its own (one ``<rede>`` each). Who speaks tells government from
member; what the turn is comes from the presidency, who calls each turn ("Wir kommen zur nächsten Hauptfrage. Für
die Fraktion … hat … das Wort.", "Eine Nachfrage?", "Die nächste Nachfrage hat …"). The presidency's words before a
turn are the chair paragraphs at the end of the previous turn (after its last own text) and the agenda paragraphs
between the two turns (``after_speeches``), so the turns are read from the parsed protocol, while that order is
still known; the store keeps no position for agenda paragraphs between speeches.

The rules, for each turn in order:

- A government speaker (``speech.speaker_group`` would be Bundesregierung, or the person spoke in a government
  office earlier in the same Befragung: 21/82 prints the minister answering as "Alois Rainer (CDU/CSU)") gives an
  ``einleitung`` before the first question, an ``antwort`` after it. An answer belongs to the open question.
- A member opens a new question (``frage``, the start of a thread) when there is none yet, or when the presidency
  calls a new one: the last of its sentences before the turn that says anything about the turn names a Hauptfrage,
  the Fragerecht, the next round or Themenkomplex, or the next question. It is a follow-up when that sentence names
  a Nachfrage, "dazu", "zu diesem Komplex", "eine weitere Frage" or a member who "hatte sich gemeldet", or when the
  member opens with "Meine Nachfrage …". Sentences saying there are none ("keine weiteren Nachfragen") say nothing
  about the next turn.
- A follow-up by the member who asked the question is a ``nachfrage``, by anyone else a ``zusatzfrage``.
- Otherwise the turn's structure decides: the member who asks again after the answer opened a question, one who
  asks once follows up. That is the case when the presidency only names the member ("Herr Bauer, bitte."), as when
  it works through a list of follow-ups it announced before, and when it names the member's fraction ("Für die
  Fraktion Die Linke hat … das Wort"): in a Fraktionsrunde that opens a question, which the asker follows with a
  Nachfrage, but the same words call the fractions' follow-ups in turn (21/58).

Measured on the 26 Befragungen of WP 21 to 2026-09-25: 46 opening statements, 450 questions, 429 Nachfragen,
629 Zusatzfragen, 1,493 answers.
"""

import re

from bdf import government
from bdf.names import GOVERNMENT, speaker_group
from bdf.parse_protocol import Protocol, Speech

BEFRAGUNG = "Befragung der Bundesregierung"  # agenda_item.kind befragung, by title (bdf/ingest.py)

_NEW = re.compile(
    r"Hauptfrage|Fragerecht|\b(nächst|erst|zweit|dritt|viert|fünft|sechst|neu)e[nr]? \w*[Rr]unde\b|Fraktionsfragen?\b"
    r"|Fragenkomplex|Themenkomplex|Hauptthem"
    r"|\b(nächsten?|ersten?|neuen?) Frage\b|\bnächsten\b\.?$|Frage der [\w/ -]*Fraktion"
)
_FOLLOW = re.compile(
    r"Nachfrage|Nachfrager|Nachfragende|nachzufragen"
    r"|\bzu diese[mr] (Komplex|Themenkomplex|Fragekomplex|Bereich|Frage|Hauptfrage|Thema|Punkt)"
    r"|\bhierzu\b|(?<!Wort )\bdazu\b|weitere[rn]? (Frage|Fragesteller|Fragestellerin|Wortmeldung)|gemeldet|Wortmeldung"
)
_FRACTION = re.compile(
    r"(?i:\b(für|seitens|aus|von)) (der |die |den |dem )?(Fraktion|[\w/ ]*-Fraktion|Unionsfraktion|CDU/CSU|SPD|AfD"
    r"|Bündnis 90/Die Grünen|Die Linke|Linken|SSW)\b"
    r"|\bzur? (Fraktion|SPD|AfD|Unionsfraktion|[\w/]+-Fraktion|CDU/CSU)\b|Fraktion\b.*\b(das Wort|dran)\b"
    r"|\bdie (SPD|AfD|Linke)\b|fraktionslos"
)
_NONE = re.compile(r"\bkeine?n?\b|nicht mehr|geschäftsordnungswidrig")
# a member who opens with "Meine Nachfrage …" or "Ich habe noch eine Nachfrage" follows up, whatever the
# presidency's call (answers thank for "die Nachfrage", members do not)
_OWN_FOLLOW = re.compile(r"\b(Meine|[Ee]ine|noch eine) Nachfrage\b|\bnachfragen\b")


def _sentences(chair: list[str]) -> list[str]:
    return [s for text in chair for s in re.split(r"(?<=[.!?])\s+|\s+–\s+", text) if s.strip()]


def call(chair: list[str]) -> str | None:
    """What the presidency's words before a turn say about it: "new" (a new question), "follow" (a follow-up), or
    None. The last sentence that says anything decides; in "Die letzte Frage zu diesem Bereich" and "eine Nachfrage
    dazu, nicht eine Hauptfrage" the follow-up comes first and wins. A sentence that only names a fraction says
    nothing of its own: "Wir kommen zur nächsten Hauptfrage. Für die CDU/CSU-Fraktion hat …" is a new question, while
    "Wir haben weitere Nachfragen. Für die Fraktion der AfD hat …" may also follow rules the presidency just
    explained, so it is left to the turn's structure."""
    said = [_said(s) for s in _sentences(chair)]
    for i in reversed(range(len(said))):
        if said[i] == "fraction":
            return "new" if i and said[i - 1] == "new" else None
        if said[i]:
            return said[i]
    return None


def _said(sentence: str) -> str | None:
    fraction = _FRACTION.search(sentence)
    if _NONE.search(sentence) and not fraction:
        return None
    follow, new = _FOLLOW.search(sentence), _NEW.search(sentence)
    if follow and new:
        # "die letzte Nachfrage zu dieser Hauptfrage" follows up; "damit noch weitere Fragen drankommen, zur nächsten
        # Hauptfrage" and "drei Wortmeldungen, bevor wir zum letzten Hauptfragenkomplex kommen" are left to the
        # structure
        if not re.match(r"[Nn]achfrag|zu diese|hierzu|dazu", follow.group()):
            return None
        return "follow" if follow.start() < new.start() or "nicht eine Hauptfrage" in sentence else "new"
    if new:
        return "new"
    if follow and not _NONE.search(sentence[: follow.start()][-25:]):
        return "follow"
    return "fraction" if fraction else None


def _chair_before(speech: Speech, previous: Speech | None, agenda_paragraphs: list[dict]) -> list[str]:
    """The presidency's words between the previous turn's last own text and this turn, without the name lines
    ("Vizepräsident Bodo Ramelow:")."""
    tail: list[str] = []
    for kind, text in reversed(previous.paragraphs if previous else []):
        if kind == "text":
            break
        if kind == "chair":
            tail.insert(0, text)
    between = [
        p["text"] for p in agenda_paragraphs if p["kind"] == "chair" and p["after_speeches"] == speech.position - 1
    ]
    return [t for t in tail + between if not t.endswith(":")]


def _is_government(speech: Speech) -> bool:
    role = speech.speaker.role
    is_government_role = bool(role) and government.parse_role(role) is not None
    return speaker_group(role, speech.speaker.fraction, is_government_role) == GOVERNMENT


def befragung_turns(speeches: list[Speech], agenda_paragraphs: list[dict]) -> list[dict]:
    """question_turn rows for the speeches of one Befragung, in order (rules in the module docstring)."""
    government_ids = {s.speaker.id for s in speeches if _is_government(s)}
    is_government = [s.speaker.id in government_ids for s in speeches]
    rows: list[dict] = []
    thread = asker = last_member = None
    for i, s in enumerate(speeches):
        if is_government[i]:
            rows.append({"speech_id": s.id, "role": "antwort" if thread else "einleitung", "thread_id": thread})
            continue
        said = call(_chair_before(s, speeches[i - 1] if i else None, agenda_paragraphs))
        if thread is None:
            new = True
        elif s.speaker.id == asker:
            new = said == "new"
        elif said == "new":
            new = True
        elif said == "follow" or s.speaker.id == last_member or _OWN_FOLLOW.search(s.text[:200]):
            new = False
        else:  # only named, or a fraction: asking again after the answer makes it a question of one's own
            following = next(
                (t for t, gov in zip(speeches[i + 1 :], is_government[i + 1 :], strict=True) if not gov), None
            )
            new = following is not None and following.speaker.id == s.speaker.id
        if new:
            thread, asker = s.id, s.speaker.id
        role = "frage" if new else "nachfrage" if s.speaker.id == asker else "zusatzfrage"
        rows.append({"speech_id": s.id, "role": role, "thread_id": thread})
        last_member = s.speaker.id
    return rows


def turns(protocol: Protocol) -> list[dict]:
    """question_turn rows (speech_id, role, thread_id, vorgang_id) for every Befragung der Bundesregierung of a
    protocol."""
    rows: list[dict] = []
    for item in protocol.agenda_items:
        if not (item["title"] or "").startswith(BEFRAGUNG):
            continue
        speeches = [s for s in protocol.speeches if s.agenda_item_id == item["id"]]
        paragraphs = [p for p in protocol.agenda_paragraphs if p["agenda_item_id"] == item["id"]]
        rows += [r | {"vorgang_id": None} for r in befragung_turns(speeches, paragraphs)]
    return rows
