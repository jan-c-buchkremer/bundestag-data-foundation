"""Download bundestag.de: Stammdaten, Plenarprotokoll XML, roll-call vote XLSX, the MdB biography list and portraits,
and the PDFs of the answers to Kleine and Große Anfragen."""

import re
import time
import zipfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import httpx

from bdf import fetch_dip, parse_biografien, protocol_status, raw
from bdf.config import raw_dir
from bdf.names import DRUCKSACHE_RE, clean_text

STAMMDATEN_URL = "https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip"
PROTOCOL_URL = "https://dserver.bundestag.de/btp/{wp}/{wp}{nr:03d}.{ext}"
VOTE_LIST_URL = "https://www.bundestag.de/ajax/filterlist/de/parlament/plenum/abstimmung/liste/462112-462112"
# the card list behind https://www.bundestag.de/abgeordnete (current members); the server returns 12 cards per page
BIO_LIST_URL = "https://www.bundestag.de/ajax/filterlist/de/abgeordnete/1040594-1040594"
BIO_PAGE = 12

_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.S)
# file names vary: 20260710_7.pdf, 20260710_7-xls.xlsx, 20260709_1_xls.xlsx
_HREF_RE = re.compile(r'href="([^"]+/(\d{8})_(\d+)(?:[-_]xlsx?)?\.(pdf|xlsx))"')
_TITLE_RE = re.compile(r"<a\b[^>]*\.pdf\"[^>]*>\s*<span>(.*?)</span>\s*<span role", re.S)


# DIP's title of an answer: "auf die Kleine Anfrage\r\n- Drucksache 21/8198 -\r\n…"
_ANSWER_TITLE = re.compile(r"^auf die (?:Kleine|Große) Anfrage\b")


def answers_dir() -> Path:
    return raw_dir() / "bundestag" / "drucksachen"


def answer_path(number: str) -> Path:
    """Where the PDF of Drucksache "21/1095" lies, named as dserver names it: drucksachen/21/2101095.pdf."""
    wp, n = number.split("/")
    return answers_dir() / wp / f"{wp}{int(n):05d}.pdf"


def answers_listed(wp: int) -> list[tuple[str, str]]:
    """(number, pdf_url) of every answer to a Kleine or Große Anfrage in the fetched DIP Drucksachen of the
    Wahlperiode (data/raw/dip/drucksache/), in number order."""
    found: dict[str, str] = {}
    for path in raw.data_files(fetch_dip.dip_dir() / "drucksache", "*.json"):
        for d in raw.read_json(path):
            url = (d.get("fundstelle") or {}).get("pdf_url")
            if (d.get("wahlperiode") == wp and d.get("drucksachetyp") == "Antwort" and d.get("herausgeber") == "BT"
                    and _ANSWER_TITLE.match(d.get("titel") or "") and url):  # fmt: skip
                found[DRUCKSACHE_RE.search(d["dokumentnummer"]).group(1)] = url  # DIP has "21/8057."
    return sorted(found.items(), key=lambda item: int(item[0].split("/")[1]))


def fetch_answer_pdfs(http: httpx.Client, wp: int, *, force: bool = False) -> list[Path]:
    """Download the PDF of every answer to a Kleine or Große Anfrage that DIP lists and is not on disk yet (all of
    them with ``force``), at DIP's request rate. Returns the downloaded paths. DIP lists an answer a day or so before
    dserver serves its PDF: a 404 is skipped and tried again on the next run."""
    new = []
    for number, url in answers_listed(wp):
        path = answer_path(number)
        if path.exists() and not force:
            continue
        try:
            raw.download(http, url, path, force=True)
            new.append(path)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            print(f"answers: {number} not on dserver yet ({url})")
        time.sleep(fetch_dip.DIP_REQUEST_INTERVAL)
    return new


def stammdaten_dir() -> Path:
    return raw_dir() / "bundestag" / "stammdaten"


def protocols_dir() -> Path:
    return raw_dir() / "bundestag" / "protocols"


def protocol_path(wp: int, nr: int) -> Path:
    return protocols_dir() / str(wp) / f"{wp}{nr:03d}.xml"


def votes_dir() -> Path:
    return raw_dir() / "bundestag" / "votes"


def vote_path(url: str) -> Path:
    """Local file for a vote XLSX/PDF URL: the original file name under votes/."""
    return votes_dir() / Path(url).name


def votes_index_path() -> Path:
    return votes_dir() / "index.json"


def fetch_stammdaten(http: httpx.Client, *, force: bool = False) -> Path:
    zip_path = raw.download(http, STAMMDATEN_URL, stammdaten_dir() / "MdB-Stammdaten.zip", force=force)
    xml_path = stammdaten_dir() / "MDB_STAMMDATEN.XML"
    if force or not xml_path.exists():
        with zipfile.ZipFile(zip_path) as z:
            xml_path.write_bytes(z.read("MDB_STAMMDATEN.XML"))
    return xml_path


def fetch_protocols(http: httpx.Client, wp: int, first: int, last: int, *, force: bool = False) -> list[Path]:
    paths = []
    for nr in range(first, last + 1):
        url = PROTOCOL_URL.format(wp=wp, nr=nr, ext="xml")
        try:
            paths.append(raw.download(http, url, protocol_path(wp, nr), force=force))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(f"  {wp}/{nr}: not published yet (404)")
                continue
            raise
    return paths


def preliminary_protocols(wp: int) -> list[Path]:
    """The protocol XML files of a Wahlperiode on disk that are still the preliminary version."""
    return [p for p in raw.data_files(protocols_dir() / str(wp), "*.xml") if protocol_status.is_preliminary(p)]


def refetch_preliminary(http: httpx.Client, wp: int, skip: Iterable[Path] = ()) -> list[tuple[Path, bool]]:
    """Download every preliminary protocol of the Wahlperiode again, replacing the file on disk (the URL stays the
    same when the final version is published); `skip` holds files just downloaded. Returns (path, still
    preliminary) per file."""
    out = []
    skipped = set(skip)
    for path in preliminary_protocols(wp):
        if path in skipped:
            continue
        nr = int(path.stem[len(str(wp)) :])
        raw.download(http, PROTOCOL_URL.format(wp=wp, nr=nr, ext="xml"), path, force=True)
        out.append((path, protocol_status.is_preliminary(path)))
    return out


def fetch_preliminary_pdfs(http: httpx.Client, wp: int) -> list[tuple[Path, bool]]:
    """The PDF of every preliminary protocol on disk (``21031.pdf`` beside ``21031.xml``): the final text is there
    long before the XML is (bdf/protocol_pdf.py). Fetched again on every run, since a PDF fetched on the day of the
    sitting may still lack pages, but written only when it changed, so the lines read from it stay cached. Returns
    (path, changed) per PDF found."""
    out = []
    for path in preliminary_protocols(wp):
        nr = int(path.stem[len(str(wp)) :])
        url, pdf = PROTOCOL_URL.format(wp=wp, nr=nr, ext="pdf"), path.with_suffix(".pdf")
        try:
            content = raw.get(http, url).content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(f"  {wp}/{nr}: no PDF yet (404)")
                continue
            raise
        changed = not pdf.exists() or pdf.read_bytes() != content
        if changed:
            pdf.write_bytes(content)
            raw.write_meta(pdf, url)
        out.append((pdf, changed))
    return out


def _parse_vote_list(fragment: str) -> list[dict]:
    """Rows of the bundestag.de roll-call list fragment: date, title, pdf/xlsx URLs."""
    items: dict[str, dict] = {}
    for row in _ROW_RE.findall(fragment):
        urls = {ext: (href, day, nr) for href, day, nr, ext in _HREF_RE.findall(row)}
        if "xlsx" not in urls:
            continue
        href, day, nr = urls["xlsx"]
        title = _TITLE_RE.search(row)
        title_text = re.sub(r"<[^>]+>", "", title.group(1)) if title else ""
        title_text = clean_text(re.sub(r"^\s*\d{2}\.\d{2}\.\d{4,6}:\s*", "", title_text))
        # the date comes from the file name; the printed label has had typos
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        items[href] = {
            "date": iso,
            "number": int(nr),
            "title": title_text,
            "xlsx_url": href,
            "pdf_url": urls.get("pdf", (None,))[0],
        }
    return list(items.values())


def fetch_votes(http: httpx.Client, start: date, end: date, *, force: bool = False) -> list[dict]:
    """Download every roll-call vote XLSX (and PDF) in [start, end]; returns the index rows."""
    found: list[dict] = []
    offset, limit = 0, 30
    while True:
        response = raw.get(http, VOTE_LIST_URL, params={"limit": limit, "offset": offset})
        rows = _parse_vote_list(response.text)
        if not rows:
            break
        for row in rows:
            d = date.fromisoformat(row["date"])
            if start <= d <= end:
                found.append(row)
        if min(date.fromisoformat(r["date"]) for r in rows) < start:
            break
        offset += limit
    for row in found:
        raw.download(http, row["xlsx_url"], vote_path(row["xlsx_url"]), force=force)
        if row["pdf_url"]:
            raw.download(http, row["pdf_url"], vote_path(row["pdf_url"]), force=force)
    index_path = votes_index_path()
    index = {r["xlsx_url"]: r for r in (raw.read_json(index_path) if index_path.exists() else [])}
    index.update({r["xlsx_url"]: r for r in found})
    raw.write_json(index_path, VOTE_LIST_URL, sorted(index.values(), key=lambda r: (r["date"], r["number"])))
    return found


def biografien_dir() -> Path:
    return raw_dir() / "bundestag" / "biografien"


def fotos_dir() -> Path:
    return raw_dir() / "bundestag" / "fotos"


def photo_path(card: parse_biografien.Card) -> Path:
    """Local file of a card's portrait: the bundestag.de image resource id plus the original extension."""
    return fotos_dir() / f"{card.image_id}{Path(card.image_url or '').suffix or '.jpg'}"


def fetch_biografien(http: httpx.Client, *, force: bool = False) -> list[parse_biografien.Card]:
    """Fetch every page of the biography list (always: it is the current state), then each portrait not on disk yet.

    The pages are stored unchanged as ``biografien/page-NNN.html``; pages left over from a longer earlier list are
    removed. Portrait URLs carry a content hash, so an existing file is never re-downloaded unless ``force``.
    """
    pages: list[tuple[str, str]] = []
    while True:
        url = f"{BIO_LIST_URL}?limit={BIO_PAGE}&offset={len(pages) * BIO_PAGE}"
        fragment = raw.get(http, url).text
        if not parse_biografien.parse(fragment):
            break
        pages.append((url, fragment))
    directory = biografien_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for old in raw.data_files(directory, "page-*.html"):
        old.unlink()
        raw.meta_path(old).unlink(missing_ok=True)
    cards = []
    for n, (url, fragment) in enumerate(pages):
        path = directory / f"page-{n:03d}.html"
        path.write_text(fragment, encoding="utf-8")
        raw.write_meta(path, url)
        cards += parse_biografien.parse(fragment)
    for card in cards:
        if card.image_url:
            raw.download(http, card.image_url, photo_path(card), force=force)
    return cards
