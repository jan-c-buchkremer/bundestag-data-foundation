"""Download bundestag.de Open Data: Stammdaten, Plenarprotokoll XML, roll-call vote XLSX."""

import re
import zipfile
from datetime import date
from pathlib import Path

import httpx

from bdf import raw
from bdf.config import raw_dir
from bdf.names import clean_text

STAMMDATEN_URL = "https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip"
PROTOCOL_URL = "https://dserver.bundestag.de/btp/{wp}/{wp}{nr:03d}.{ext}"
VOTE_LIST_URL = "https://www.bundestag.de/ajax/filterlist/de/parlament/plenum/abstimmung/liste/462112-462112"

_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.S)
# file names vary: 20260710_7.pdf, 20260710_7-xls.xlsx, 20260709_1_xls.xlsx
_HREF_RE = re.compile(r'href="([^"]+/(\d{8})_(\d+)(?:[-_]xlsx?)?\.(pdf|xlsx))"')
_TITLE_RE = re.compile(r"<a\b[^>]*\.pdf\"[^>]*>\s*<span>(.*?)</span>\s*<span role", re.S)


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
