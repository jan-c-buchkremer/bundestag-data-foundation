"""Raw source files on disk.

Every downloaded file gets a sidecar ``<file>.meta.json`` with the URL it came from and
the retrieval time. That sidecar is the provenance root for everything parsed from the
file; ``ingest`` never touches the network.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bdf.config import USER_AGENT


@dataclass(frozen=True)
class RawMeta:
    url: str
    retrieved_at: str

    def provenance(self, document_id: str, *, url: str | None = None) -> dict[str, str]:
        """The three provenance columns every fact table carries (docs/design.md)."""
        return {"source_url": url or self.url, "source_document_id": document_id, "retrieved_at": self.retrieved_at}


def meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def read_meta(path: Path) -> RawMeta:
    d = json.loads(meta_path(path).read_text(encoding="utf-8"))
    return RawMeta(url=d["url"], retrieved_at=d["retrieved_at"])


def write_meta(path: Path, url: str) -> RawMeta:
    meta = RawMeta(url=url, retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"))
    meta_path(path).write_text(json.dumps(meta.__dict__, indent=1), encoding="utf-8")
    return meta


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)


def get(
    http: httpx.Client,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = 4,
) -> httpx.Response:
    """GET with a simple backoff on 429/5xx. Raises on the final failure."""
    for attempt in range(retries):
        response = http.get(url, params=params, headers=headers)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(2 ** (attempt + 1))
            continue
        break
    response.raise_for_status()
    return response


def download(http: httpx.Client, url: str, dest: Path, *, force: bool = False) -> Path:
    """Download ``url`` to ``dest`` unless it is already there. Writes the sidecar."""
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = get(http, url)
    dest.write_bytes(response.content)
    write_meta(dest, url)
    return dest


def write_json(dest: Path, url: str, payload: object) -> Path:
    """Store an API result (possibly merged over several pages) as a raw file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    write_meta(dest, url)
    return dest


def cached_json(dest: Path, url: str, produce: Callable[[], object], *, force: bool = False) -> object:
    """Return the raw JSON file at ``dest``, producing and storing it first unless it exists."""
    if dest.exists() and not force:
        return read_json(dest)
    payload = produce()
    write_json(dest, url, payload)
    return payload


def data_files(directory: Path, pattern: str) -> list[Path]:
    """Raw files matching ``pattern`` under ``directory``, without the .meta.json sidecars."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob(pattern) if not p.name.endswith(".meta.json"))


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
