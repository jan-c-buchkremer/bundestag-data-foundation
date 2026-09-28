"""Parse the bundestag.de MdB biography list (the ajax filterlist behind bundestag.de/abgeordnete).

Each page is an HTML fragment of up to 12 cards. A card has the printed name ("Abdi, Sanae", "Aken, Jan van",
"Schneider (Erfurt), Carsten"), the fraction, the biography URL, a portrait in several crops and sizes, and the
image credit in the caption ("© Sanae Abdi/SPD-Fraktion"). The credit is the photographer or rights holder as
bundestag.de prints it; it is kept verbatim without the "©".
"""

import html
import re
from dataclasses import dataclass

from bdf.names import clean_text, normalize_fraction

_CARD_RE = re.compile(r"<article\b.*?</article>", re.S)
_BIO_RE = re.compile(r'<a href="([^"]+/abgeordnete/biografien/[^"]+)"')
_TITLE_RE = re.compile(r'e-teaserCardProfile__title"[^>]*>\s*<p>(.*?)</p>', re.S)
_TEXT_RE = re.compile(r'e-teaserCardProfile__text"[^>]*>\s*<p>(.*?)</p>', re.S)
_SRCSET_RE = re.compile(r'<source srcset="([^"]+)"')
_INFO_RE = re.compile(r'e-pictureCompact__infoText"[^>]*>(.*?)</div>', re.S)
_P_RE = re.compile(r"<p>(.*?)</p>", re.S)
_IMAGE_ID_RE = re.compile(r"/resource/image/(\d+)/")
# name particles that bundestag.de puts after the given name ("Aken, Jan van")
_PARTICLES = {"von", "van", "de", "der", "den", "dos", "da", "di", "zu", "la", "le"}


@dataclass
class Card:
    last_name: str  # without the Ortszusatz and without particles
    first_name: str  # without academic titles
    printed_name: str  # as on the card, "Schneider (Erfurt), Carsten"
    fraction: str | None  # normalised as everywhere in the store
    bio_url: str
    image_url: str | None  # the largest rendition offered (3:4, 864 px wide)
    image_id: str | None  # bundestag.de resource id of the image, stable across renditions
    credit: str | None


def _text(fragment: str) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", "", fragment)))


def split_name(printed: str) -> tuple[str, str]:
    """ "Schneider (Erfurt), Carsten" -> ("Schneider", "Carsten"); "Aken, Jan van" -> ("van Aken", "Jan");
    "Alhamwi, Dr. Alaa" -> ("Alhamwi", "Alaa")."""
    last, _, first = printed.partition(",")
    last = re.sub(r"\s*\(.*?\)", "", last).strip()
    # academic titles before the given name: "Dr.", "Prof. Dr.-Ing. habil.", "Dr. h. c."
    first = re.sub(r"\b(Prof|Dr|Dipl)\.(-[\w]+\.)?\s*|\bhabil\.\s*|\bh\.\s?c\.\s*", "", first).strip()
    words = first.split()
    particles = []
    while len(words) > 1 and words[-1] in _PARTICLES:
        particles.insert(0, words.pop())
    return " ".join([*particles, last]), " ".join(words)


def _largest(srcsets: list[str]) -> str | None:
    """The widest URL over all <source srcset> entries ("url 2x, url 1x")."""
    best, width = None, -1
    for srcset in srcsets:
        for candidate in srcset.split(","):
            url = candidate.strip().split(" ")[0]
            m = re.search(r"/resource/image/\d+/[^/]+/(\d+)/", url)
            if m and int(m.group(1)) > width:
                best, width = url, int(m.group(1))
    return best


def parse(fragment: str) -> list[Card]:
    cards = []
    for article in _CARD_RE.findall(fragment):
        bio = _BIO_RE.search(article)
        title = _TITLE_RE.search(article)
        if not bio or not title:
            continue
        printed = _text(title.group(1))
        last, first = split_name(printed)
        text = _TEXT_RE.search(article)
        image_url = _largest(_SRCSET_RE.findall(article))
        info = _INFO_RE.search(article)
        lines = [_text(p) for p in _P_RE.findall(info.group(1))] if info else []
        credit = next((line for line in lines if line.startswith("©")), None)
        cards.append(
            Card(
                last_name=last,
                first_name=first,
                printed_name=printed,
                fraction=normalize_fraction(_text(text.group(1))) if text else None,
                bio_url=bio.group(1),
                image_url=image_url,
                image_id=m.group(1) if image_url and (m := _IMAGE_ID_RE.search(image_url)) else None,
                credit=credit.lstrip("©").strip() if credit else None,
            )
        )
    return cards
