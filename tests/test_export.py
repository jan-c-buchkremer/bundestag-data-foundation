import csv
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from bdf import cli, export


def _rows(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def test_column_comments_parse_the_schema() -> None:
    comments = export.column_comments()
    assert comments[("sitting", "id")] == '"21/94"'
    # continuation lines are joined
    assert comments[("interjection", "kind")].endswith("| zustimmung | unruhe | other")
    assert comments[("government_role", "source_kind")].startswith("wikidata | stammdaten | protocol")


def test_export_writes_every_table_with_a_datapackage(store: sqlite3.Connection, tmp_path: Path) -> None:
    target = tmp_path / "export"
    summary = export.write_export(store, target)

    tables = [r[0] for r in store.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    package = json.loads((target / "datapackage.json").read_text("utf-8"))
    assert sorted(r["name"] for r in package["resources"]) == sorted(tables)
    assert package["latest_sitting_date"] == store.execute("SELECT max(date) FROM sitting").fetchone()[0]
    assert {s["name"] for s in summary} == {f"{t}.csv.gz" for t in tables} | {"datapackage.json", "README.md"}
    assert sorted(p.name for p in target.iterdir()) == sorted(s["name"] for s in summary)
    assert not list(tmp_path.glob(".export.*"))  # no temp dir left

    speech = next(r for r in package["resources"] if r["name"] == "speech")
    fields = {f["name"]: f for f in speech["schema"]["fields"]}
    assert speech["schema"]["primaryKey"] == ["id"]
    assert fields["position"]["type"] == "integer"
    assert fields["source_document_id"]["description"].startswith("citable source pointer")
    assert fields["person_id"]["description"] == "→ person.id"
    assert {"fields": "sitting_id", "reference": {"resource": "sitting", "fields": "id"}} in speech["schema"][
        "foreignKeys"
    ]
    assert speech["licenses"][0]["path"] == "https://www.bundestag.de/nutzungsbedingungen"
    vd = next(r for r in package["resources"] if r["name"] == "vorgang_drucksache")
    assert vd["schema"]["primaryKey"] == ["vorgang_id", "drucksache_id"]
    assert "Die Bundeswahlleiterin, Wiesbaden 2025" in (target / "README.md").read_text("utf-8")

    # rows, header and order by primary key; NULL as an empty field
    rows = _rows(target / "speech.csv.gz")
    assert rows[0] == [f["name"] for f in speech["schema"]["fields"]]
    ids = [r[0] for r in rows[1:]]
    assert ids == sorted(ids) and len(ids) == store.execute("SELECT count(*) FROM speech").fetchone()[0]
    col = rows[0].index("agenda_item_id")
    nulls = store.execute("SELECT count(*) FROM speech WHERE agenda_item_id IS NULL").fetchone()[0]
    assert sum(1 for r in rows[1:] if r[col] == "") == nulls


def test_export_leaves_out_local_paths(store: sqlite3.Connection, tmp_path: Path) -> None:
    export.write_export(store, tmp_path / "export")
    header = _rows(tmp_path / "export" / "person_photo.csv.gz")[0]
    assert "local_path" not in header and "credit" in header


def test_export_is_deterministic_and_replaces_the_old_one(store: sqlite3.Connection, tmp_path: Path) -> None:
    target = tmp_path / "export"
    export.write_export(store, target)
    first = (target / "interjection.csv.gz").read_bytes()
    (target / "stale.csv.gz").write_text("old")
    export.write_export(store, target)
    assert (target / "interjection.csv.gz").read_bytes() == first
    assert not (target / "stale.csv.gz").exists()


def test_failed_export_keeps_the_previous_one(
    store: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "export"
    export.write_export(store, target)
    before = sorted(p.name for p in target.iterdir())

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(export, "_readme", boom)
    with pytest.raises(OSError):
        export.write_export(store, target)
    assert sorted(p.name for p in target.iterdir()) == before
    assert not list(tmp_path.glob(".export.*"))


def test_cli_export_prints_a_size_summary(store: sqlite3.Connection, tmp_path: Path, capsys) -> None:
    cli.main(["export", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert "speech.csv.gz" in out and out.splitlines()[-1].startswith("total")
    assert (tmp_path / "out" / "datapackage.json").exists()
