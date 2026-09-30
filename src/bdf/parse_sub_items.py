"""Sub-items of an agenda item, and whether the item was called up without a debate.

A ``<tagesordnungspunkt>`` is normally one item, but the presidency sometimes calls up a block of
many items voted one by one ("Ich rufe auf die Tagesordnungspunkte 41b bis 41s. Es handelt sich um die
Beschlussfassung zu Vorlagen, zu denen keine Aussprache vorgesehen ist.") and the protocol prints all of
them in one element. Each is announced by a chair paragraph that is nothing but the call-up
("Tagesordnungspunkt 41c:", "Zusatzpunkt 8:", "Wir kommen zu Tagesordnungspunkt 12k:"), followed by
that item's title lines (procedural paragraphs), its Drucksache lines and the chair's voting words.

``split`` cuts an item's paragraphs at those call-ups. Only an item with at least two distinct call-ups
is split; the other items stay the unit. Deliberately not split (docs/decisions.md):

- "Ich rufe auf die Tagesordnungspunkte 7a und 7b:" (one debate over several lettered items) and
  call-ups with words after the number ("Tagesordnungspunkt 7a: Wir kommen zur Abstimmung über …"): the
  parts share a debate and their titles are one list;
- lettered lists inside the titles ("40 a) …", "b) …") that no call-up announces (Überweisungen im
  vereinfachten Verfahren).

``no_debate`` is set where the chair says that no Aussprache is provided.
"""

import json
import re

from bdf.names import DRUCKSACHE_RE

TITLE_CLASSES_SKIPPED = {"T_Drs", "T_Ueberweisung"}

_BARE = r"(Tagesordnungspunkt|Zusatzpunkt|TOP|ZP)\s+(\d+)\s*([a-z])?"
_CALL_UP = re.compile(rf"^{_BARE}\s*:?$")
_CALL_UP_LEAD = re.compile(rf"^(?:Wir kommen|Ich rufe)(?: jetzt| nun| damit)?(?: zu(?:m)?| auf(?: den)?) {_BARE}\s*:$")

_NO_DEBATE = re.compile(r"\b(?:keine|ohne) (?:Aussprache|Debatte)\b|\bAussprache\b[^.!?]{0,60}\bnicht vorgesehen")
_NOT_WHOLE = re.compile(r"\b(?:ersten|zweiten|dritten) Beratung")  # "Eine Aussprache in der zweiten Beratung …"


def call_up(text: str) -> str | None:
    """The label ("41c", "ZP8", "ZP1a") of a paragraph that is only a call-up, else None."""
    t = text.strip()
    m = _CALL_UP.match(t) or _CALL_UP_LEAD.match(t)
    if not m:
        return None
    kind, number, letter = m.groups()
    return f"{'ZP' if kind in ('Zusatzpunkt', 'ZP') else ''}{number}{letter or ''}"


def says_no_debate(text: str) -> bool:
    return any(_NO_DEBATE.search(s) and not _NOT_WHOLE.search(s) for s in re.split(r"(?<=[.!?])\s+", text))


def _lines(paragraphs: list[dict]) -> tuple[list[str], list[str]]:
    """Title lines and Drucksache numbers of the T_* paragraphs, as the agenda item's own are read."""
    title, numbers = [], []
    for p in paragraphs:
        klasse = p.get("klasse") or ""
        if p["kind"] != "procedural" or not klasse.startswith("T_"):
            continue
        numbers += [n for n in DRUCKSACHE_RE.findall(p["text"]) if n not in numbers]
        if klasse not in TITLE_CLASSES_SKIPPED:
            title.append(p["text"])
    return title, numbers


def split(agenda_items: list[dict], paragraphs: list[dict]) -> tuple[list[dict], dict[str, bool]]:
    """(sub-item rows without provenance, no_debate per agenda item id). The rows carry id,
    agenda_item_id, label, position, title, drucksache_numbers (JSON), no_debate, first_paragraph and
    last_paragraph (positions of the agenda item's paragraphs).

    The protocol sometimes ends an element right after the call-up of a block ("Ich rufe auf die
    Tagesordnungspunkte 26a bis 26m … keine Aussprache vorgesehen") and starts the next one with the first
    "Tagesordnungspunkt 26a:"; an item that starts with a bare call-up therefore inherits a no-debate
    statement among the last two chair paragraphs of the previous item."""
    by_item: dict[str, list[dict]] = {a["id"]: [] for a in agenda_items}
    for p in paragraphs:
        by_item[p["agenda_item_id"]].append(p)
    subs: list[dict] = []
    item_no_debate: dict[str, bool] = {}
    tail = False  # the previous item's last chair paragraphs say that no Aussprache is provided
    for item_id, paras in by_item.items():
        starts: list[tuple[int, str]] = []  # (index into paras, label)
        for i, p in enumerate(paras):
            label = call_up(p["text"]) if p["kind"] == "chair" else None
            if label and label not in {lb for _, lb in starts}:
                starts.append((i, label))
        inherited = tail and bool(paras) and paras[0]["kind"] == "chair" and _CALL_UP.match(paras[0]["text"].strip())
        tail = any(says_no_debate(p["text"]) for p in [p for p in paras if p["kind"] == "chair"][-2:])
        item_no_debate[item_id] = bool(inherited) or any(_says(p) for p in paras)
        if len(starts) < 2:
            continue
        for k, (start, label) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(paras)
            span = paras[start:end]
            title, numbers = _lines(span)
            subs.append(
                {
                    "id": f"{item_id}/{label}", "agenda_item_id": item_id, "label": label, "position": k + 1,
                    "title": " | ".join(title) or None, "drucksache_numbers": json.dumps(numbers),
                    "no_debate": int(bool(inherited) or any(_says(p) for p in paras[:end])),
                    "first_paragraph": span[0]["position"], "last_paragraph": span[-1]["position"],
                }
            )  # fmt: skip
    return subs, item_no_debate


def _says(p: dict) -> bool:
    return p["kind"] == "chair" and says_no_debate(p["text"])
