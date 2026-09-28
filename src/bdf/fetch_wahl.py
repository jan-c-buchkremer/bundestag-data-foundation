"""Download the official results of a Bundestag election from the Bundeswahlleiterin.

Two open-data files per election, published once after the final result and not changed since:
the elected candidates (``btw25_gewaehlte_utf8.zip``) and the results per Wahlkreis (``kerg2.csv``); and the
Wahlkreiseinteilung, the Gemeinden of each Wahlkreis (``btw25_wkr_gemeinden_20241130_utf8.csv``), published before
the election.
Licence: Datenlizenz Deutschland – Namensnennung 2.0, see docs/licences.md.
"""

import zipfile
from pathlib import Path

import httpx

from bdf import raw
from bdf.config import raw_dir

BWL = "https://www.bundeswahlleiterin.de"
ELECTIONS = {
    "btw25": {
        "gewaehlte": f"{BWL}/dam/jcr/eeb02132-caeb-430a-ae1f-6cd5907f1809/btw25_gewaehlte_utf8.zip",
        "kerg2": f"{BWL}/bundestagswahlen/2025/ergebnisse/opendata/btw25/csv/kerg2.csv",
        "gemeinden": f"{BWL}/dam/jcr/aa868597-0e60-476c-bd2b-279c1e9a142a/btw25_wkr_gemeinden_20241130_utf8.csv",
    },
}
# the election whose result formed each Wahlperiode
ELECTION_OF_WAHLPERIODE = {21: "btw25"}
GEWAEHLTE_CSV = "{election}_gewaehlte_utf8.csv"


def wahl_dir(election: str) -> Path:
    return raw_dir() / "bundeswahlleiterin" / election


def gewaehlte_zip(election: str) -> Path:
    return wahl_dir(election) / Path(ELECTIONS[election]["gewaehlte"]).name


def gewaehlte_csv(election: str) -> Path:
    return wahl_dir(election) / GEWAEHLTE_CSV.format(election=election)


def kerg2_csv(election: str) -> Path:
    return wahl_dir(election) / "kerg2.csv"


def gemeinden_csv(election: str) -> Path:
    return wahl_dir(election) / Path(ELECTIONS[election]["gemeinden"]).name


def fetch_election(http: httpx.Client, election: str, *, force: bool = False) -> list[Path]:
    """Download the three files unless they are already there; the CSV inside the zip is extracted next to it
    and takes its provenance from the zip's sidecar (as the Stammdaten XML does)."""
    urls = ELECTIONS[election]
    zip_path = raw.download(http, urls["gewaehlte"], gewaehlte_zip(election), force=force)
    csv_path = gewaehlte_csv(election)
    if force or not csv_path.exists():
        with zipfile.ZipFile(zip_path) as z:
            csv_path.write_bytes(z.read(csv_path.name))
    kerg2 = raw.download(http, urls["kerg2"], kerg2_csv(election), force=force)
    gemeinden = raw.download(http, urls["gemeinden"], gemeinden_csv(election), force=force)
    return [csv_path, kerg2, gemeinden]
