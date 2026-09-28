"""Download the federal government roster from Wikidata, and the Commons portraits (P18) of its members.

Two SPARQL queries against query.wikidata.org (a class/subclass path in one query times out):
1. ``positions.json``: the position items that are a subclass or instance of "Bundesminister" (Q248352),
   "Parlamentarischer Staatssekretär" (Q19731005) or "beamteter Staatssekretär" (Q22703996).
2. ``government.json``: every "position held" (P39) statement on those positions, plus a few fixed ones (Kanzler,
   Chef des Bundeskanzleramtes, Staatsminister, BKM), that starts on or after the day the current government took
   office, with end date, department, and the holder's birth date, current party and image.
Then ``commons.json``: Commons ``imageinfo`` (thumbnail URL, author, licence) for those images, and the
thumbnails under ``wikidata/fotos/``. All responses are stored unchanged. Wikidata is CC0; Commons files
carry their own licences, recorded as the credit.
"""

from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from bdf import raw
from bdf.config import raw_dir
from bdf.parse_wikidata import KIND_OF_CLASS

SPARQL_URL = "https://query.wikidata.org/sparql"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# the Merz cabinet took office on 6 May 2025
GOVERNMENT_START = "2025-05-06"
THUMB_WIDTH = 864

# position classes whose subclasses and instances are collected by the first query; every other key of
# KIND_OF_CLASS is a single position queried as it is (the subclasses of Staatsminister are Land offices)
CLASSES = ("Q248352", "Q19731005", "Q22703996")

POSITIONS_QUERY = """
SELECT ?pos ?class WHERE {
  VALUES ?class { %s }
  { ?pos wdt:P279 ?class } UNION { ?pos wdt:P31 ?class }
}
"""

ROLES_QUERY = """
SELECT ?st ?person ?personLabel ?pos ?posLabel ?class ?start ?end ?dept ?deptLabel ?birth ?party ?partyShort
       ?partyLabel ?image ?familyLabel WHERE {
  VALUES (?pos ?class) { %s }
  ?st ps:P39 ?pos ; pq:P580 ?start .
  ?person p:P39 ?st .
  FILTER(?start >= "%sT00:00:00Z"^^xsd:dateTime)
  OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?st pq:P642 ?of }
  OPTIONAL { ?pos wdt:P2389 ?directs }
  BIND(COALESCE(?of, ?directs) AS ?dept)
  OPTIONAL { ?person wdt:P569 ?birth }
  OPTIONAL {
    ?person p:P102 ?partyStatement . ?partyStatement ps:P102 ?party .
    FILTER NOT EXISTS { ?partyStatement pq:P582 [] }
    OPTIONAL { ?party wdt:P1813 ?partyShort FILTER(LANG(?partyShort) = "de") }
  }
  OPTIONAL { ?person wdt:P18 ?image }
  OPTIONAL { ?person wdt:P734 ?family }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,en". }
}
"""


def wikidata_dir() -> Path:
    return raw_dir() / "wikidata"


def positions_path() -> Path:
    return wikidata_dir() / "positions.json"


def government_path() -> Path:
    return wikidata_dir() / "government.json"


def commons_path() -> Path:
    return wikidata_dir() / "commons.json"


def fotos_dir() -> Path:
    return wikidata_dir() / "fotos"


def commons_title(image_url: str) -> str:
    """'http://commons.wikimedia.org/wiki/Special:FilePath/Stefanie%20Hubig.jpg' -> 'File:Stefanie Hubig.jpg'."""
    return "File:" + unquote(image_url.rsplit("/", 1)[-1])


def photo_path(title: str) -> Path:
    """Local file for a Commons thumbnail; the file name without 'File:' and with spaces as underscores."""
    return fotos_dir() / title.removeprefix("File:").replace(" ", "_").replace("/", "_")


def _sparql(http: httpx.Client, query: str) -> tuple[str, dict]:
    url = f"{SPARQL_URL}?query={quote(query)}"
    response = raw.get(http, SPARQL_URL, params={"query": query}, headers={"Accept": "application/sparql-results+json"})
    return url, response.json()


def _values(rows: list[tuple[str, str]]) -> str:
    return " ".join(f"(wd:{pos} wd:{cls})" for pos, cls in rows)


def fetch_government(http: httpx.Client) -> list[dict]:
    """Both SPARQL queries (always: the roster is current state), then the Commons portraits not on disk yet."""
    url, positions = _sparql(http, POSITIONS_QUERY % " ".join(f"wd:{q}" for q in CLASSES))
    pairs = {(q, q) for q in KIND_OF_CLASS}
    for b in positions["results"]["bindings"]:
        pairs.add((b["pos"]["value"].rsplit("/", 1)[-1], b["class"]["value"].rsplit("/", 1)[-1]))
    roles_url, roles = _sparql(http, ROLES_QUERY % (_values(sorted(pairs)), GOVERNMENT_START))
    raw.write_json(positions_path(), url, positions)
    raw.write_json(government_path(), roles_url, roles)
    bindings = roles["results"]["bindings"]
    fetch_commons(http, sorted({commons_title(b["image"]["value"]) for b in bindings if "image" in b}))
    return bindings


def fetch_commons(http: httpx.Client, titles: list[str]) -> None:
    """imageinfo (thumbnail URL, author, licence) for ``titles`` in batches of 50, stored as one list of pages."""
    pages: list[dict] = []
    for i in range(0, len(titles), 50):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": str(THUMB_WIDTH),
            "titles": "|".join(titles[i : i + 50]),
        }
        pages += raw.get(http, COMMONS_API, params=params).json()["query"]["pages"]
    raw.write_json(commons_path(), f"{COMMONS_API}?action=query&prop=imageinfo&titles=<P18 of the roster>", pages)
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("thumburl"):
            raw.download(http, info["thumburl"], photo_path(page["title"]))
