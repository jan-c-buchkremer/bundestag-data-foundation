"""health: snapshot of the store, compared with the last run's; only clear regressions fail."""

import json
from datetime import date

from bdf import health


def snap(tables=None, problems=None):
    base = {name: 0 for name in health.PROBLEMS}
    return {"tables": tables or {"speech": 1000}, "problems": {**base, **(problems or {})}}


def test_growth_and_small_changes_pass():
    before = snap({"speech": 1000, "agenda_item": 100}, {"protocol_gaps": 20, "unmatched_votes": 3})
    now = snap({"speech": 1100, "agenda_item": 98}, {"protocol_gaps": 25, "unmatched_votes": 50})
    report = health.compare(now, before, "2026-10-03")
    assert report.failures == []
    assert "  speech: 1,000 → 1,100 (+100)" in report.lines
    assert any(line.startswith("  protocol_gaps: 25 (was 20)") for line in report.lines)


def test_empty_or_shrunken_table_fails():
    report = health.compare(snap({"speech": 0, "drucksache": 900}), snap({"speech": 1000, "drucksache": 1000}))
    assert report.failures == ["table speech is empty (had 1,000 rows)", "table drucksache lost 100 of 1,000 rows"]


def test_problem_jump_past_its_threshold_fails_report_only_never():
    before = snap(problems={"protocol_gaps": 20, "preliminary_sittings": 1})
    now = snap(problems={"protocol_gaps": 31, "preliminary_sittings": 40})
    assert health.compare(now, before).failures == ["protocol_gaps rose from 20 to 31 (more than 10)"]


def test_first_snapshot_and_new_table_never_fail():
    assert health.compare(snap(), None).failures == []
    report = health.compare(snap({"speech": 1000, "question_activity": 5}), snap({"speech": 1000}))
    assert report.failures == [] and "  question_activity: new, 5 rows" in report.lines


def test_check_compares_with_the_newest_earlier_snapshot(store, data_dir):
    health.health_dir().mkdir(parents=True)
    for day, n in (("2026-10-01", 1), ("2026-10-02", 2)):
        (health.health_dir() / f"{day}.json").write_text(json.dumps(snap({"speech": n})))
    report = health.check(store, date(2026, 10, 3))
    assert report.previous == "2026-10-02"
    saved = json.loads((health.health_dir() / "2026-10-03.json").read_text())
    assert saved["tables"]["speech"] == store.execute("SELECT COUNT(*) FROM speech").fetchone()[0]
    assert set(saved["problems"]) == set(health.PROBLEMS)
    # a second run on the same day still compares with the day before, not with itself
    assert health.check(store, date(2026, 10, 3)).previous == "2026-10-02"
