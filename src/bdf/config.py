"""Paths and environment. Everything lives under one data directory (BDF_DATA_DIR, default ./data)."""

import os
from pathlib import Path

DIP_BASE_URL = "https://search.dip.bundestag.de/api/v1"
AW_BASE_URL = "https://www.abgeordnetenwatch.de/api/v2"
USER_AGENT = "bundestag-data-foundation/0.1 (+https://github.com/jan-c-buchkremer/bundestag-data-foundation)"


def data_dir() -> Path:
    return Path(os.environ.get("BDF_DATA_DIR", "data")).resolve()


def raw_dir() -> Path:
    return data_dir() / "raw"


def db_path() -> Path:
    return data_dir() / "bundestag.sqlite"


def dip_api_key() -> str:
    return os.environ.get("DIP_API_KEY", "")
