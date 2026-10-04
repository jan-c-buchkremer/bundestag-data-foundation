"""Health report after each ingest: rows per table and the known problem counts, compared with the last run.

`update` writes one snapshot per day to data/health/<date>.json and compares it with the newest earlier one. The run
fails (exit code 1, which Gatus turns into an ntfy alert) only when something clearly got worse: a table that had
rows is empty, a table lost more than TABLE_DROP of its rows, or a problem count rose by more than its threshold.
Every other change is printed and does not fail the run.
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bdf import queries
from bdf.config import data_dir

# a table that loses more than this share of its rows from one run to the next fails the run
TABLE_DROP = 0.05


def _count(sql: str) -> Callable[[sqlite3.Connection], int]:
    return lambda conn: conn.execute(sql).fetchone()[0]


def _protocol_gaps(preliminary: bool) -> Callable[[sqlite3.Connection], int]:
    """Beratungen without an agenda item; ``preliminary``: only those whose protocol has no PDF part yet (expected
    until the final PDF is out), else the others (the protocol has the pages: a parser or DIP problem)."""
    return lambda conn: sum(
        1 for s in queries.protocol_gaps(conn) for b in s["beratungen"] if (b["cause"] == "preliminary") == preliminary
    )


# name -> (count, increase that fails the run or None for "report only", what it counts)
PROBLEMS: dict[str, tuple[Callable[[sqlite3.Connection], int], int | None, str]] = {
    "protocol_gaps": (_protocol_gaps(False), 10, "DIP Beratungen without an agenda item (bdf query protocol-gaps)"),
    "protocol_gaps_preliminary": (_protocol_gaps(True), None,
                                  "the same in preliminary protocols whose final PDF is not read yet"),
    "preliminary_sittings": (_count("SELECT COUNT(*) FROM sitting WHERE preliminary = 1"), None,
                             "sittings whose protocol is still the preliminary version"),
    "stale_roles": (lambda conn: len(queries.stale_roles(conn)), None,
                    "protocol-only government roles not seen for a while (bdf query stale-roles)"),
    "unmatched_votes": (_count("SELECT COUNT(*) FROM individual_vote WHERE person_id IS NULL"), 100,
                        "roll-call vote rows without a person"),
    "unlinked_roll_calls": (_count("SELECT COUNT(*) FROM roll_call_vote WHERE vorgang_id IS NULL"), 5,
                            "roll-call votes without a Vorgang"),
    "placeholder_speakers": (_count("SELECT COUNT(*) FROM person WHERE id LIKE 'pdf-%'"), None,
                             "speakers only a PDF names (pdf- placeholders)"),
    "vorlagen_without_vorgang": (_count("SELECT COUNT(*) FROM agenda_item_vorlage WHERE vorgang_id IS NULL"), 150,
                                 "Drucksachen on agenda items without a Vorgang"),
    "unresolved_authors": (_count("SELECT COUNT(DISTINCT dip_person_id) FROM drucksache_author "
                                  "WHERE person_id IS NULL"), None,
                           "DIP persons on Drucksachen without a person (mostly non-MdBs)"),
    "askers_without_person": (_count("SELECT COUNT(DISTINCT dip_person_id) FROM question_activity "
                                     "WHERE activity_type IN ('Frage', 'Zusatzfrage') AND person_id IS NULL"), 10,
                              "askers of Fragen without a person"),
    "unmatched_elected": (_count("SELECT COUNT(*) FROM election_candidacy WHERE person_id IS NULL"), 3,
                          "elected candidates without a person"),
    "unmatched_successors": (_count("SELECT COUNT(*) FROM mandate_successor WHERE person_id IS NULL"), 3,
                             "Mandatsnachfolger without a person"),
}  # fmt: skip


def snapshot(conn: sqlite3.Connection) -> dict:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return {
        "tables": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables},
        "problems": {name: count(conn) for name, (count, _, _) in PROBLEMS.items()},
    }


@dataclass
class Report:
    previous: str | None  # date of the snapshot compared with
    lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def compare(now: dict, before: dict | None, previous: str | None = None) -> Report:
    report = Report(previous)
    if before is None:
        report.lines.append("health: first snapshot, nothing to compare with")
        before = {"tables": {}, "problems": {}}
    else:
        report.lines.append(f"health: compared with the snapshot of {previous}")
    for table, n in now["tables"].items():
        was = before["tables"].get(table)
        if was is None and before["tables"]:
            report.lines.append(f"  {table}: new, {n:,} rows")
        if was is None or was == n:
            continue
        report.lines.append(f"  {table}: {was:,} → {n:,} ({n - was:+,})")
        if was > 0 and n == 0:
            report.failures.append(f"table {table} is empty (had {was:,} rows)")
        elif n < was * (1 - TABLE_DROP):
            report.failures.append(f"table {table} lost {was - n:,} of {was:,} rows")
    for table, was in before["tables"].items():
        if table not in now["tables"] and was:
            report.failures.append(f"table {table} is gone (had {was:,} rows)")
    for name, n in now["problems"].items():
        was = before["problems"].get(name)
        _, limit, what = PROBLEMS[name]
        change = "" if was is None or was == n else f" (was {was:,})"
        report.lines.append(f"  {name}: {n:,}{change} — {what}")
        if was is not None and limit is not None and n - was > limit:
            report.failures.append(f"{name} rose from {was:,} to {n:,} (more than {limit})")
    for f in report.failures:
        report.lines.append(f"  FAIL: {f}")
    return report


def health_dir() -> Path:
    return data_dir() / "health"


def latest_before(day: date) -> tuple[str, dict] | None:
    """The newest snapshot written before ``day``, as (date, snapshot)."""
    files = sorted(p for p in health_dir().glob("*.json") if p.stem < day.isoformat())
    return (files[-1].stem, json.loads(files[-1].read_text())) if files else None


def check(conn: sqlite3.Connection, today: date, *, save: bool = True) -> Report:
    """Snapshot the store, compare it with the newest earlier snapshot and, with ``save``, keep today's."""
    now = snapshot(conn)
    found = latest_before(today)
    report = compare(now, found[1] if found else None, found[0] if found else None)
    if save:
        health_dir().mkdir(parents=True, exist_ok=True)
        (health_dir() / f"{today.isoformat()}.json").write_text(json.dumps(now, indent=1, sort_keys=True))
    return report
