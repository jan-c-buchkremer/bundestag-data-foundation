"""Fraction vocabulary and speaker groups (bdf/names.py, ingest.ingest_groups)."""

import json

import pytest

from bdf.names import originator_group, party_fraction, speaker_group


@pytest.mark.parametrize(
    ("party", "fraction"),
    [("CDU", "CDU/CSU"), ("CSU", "CDU/CSU"), ("GRÜNE", "BÜNDNIS 90/DIE GRÜNEN"), ("DIE LINKE.", "Die Linke"),
     ("Die Linke", "Die Linke"), ("AfD", "AfD"), ("SSW", None), ("FDP", None), (None, None)],
)  # fmt: skip
def test_party_fraction(party, fraction):
    assert party_fraction(party) == fraction


@pytest.mark.parametrize(
    ("title", "group"),
    [("Fraktion der SPD", "SPD"), ("Fraktion DIE LINKE", "Die Linke"), ("Fraktion BÜNDNIS 90/DIE GRÜNEN",
     "BÜNDNIS 90/DIE GRÜNEN"), ("Fraktion der CDU/CSU", "CDU/CSU"), ("Bundesregierung", "Bundesregierung"),
     ("Bundesministerium der Finanzen", "Bundesregierung"), ("Haushaltsausschuss", None), ("Bundesrat", None),
     ("Präsident des Deutschen Bundestages", None)],
)  # fmt: skip
def test_originator_group(title, group):
    assert originator_group(title) == group


@pytest.mark.parametrize(
    ("role", "fraction", "government", "group"),
    [(None, "SPD", False, "SPD"),
     ("Bundeskanzler", None, True, "Bundesregierung"),
     ("Beauftragte der Bundesregierung für Ostdeutschland", None, False, "Bundesregierung"),
     ("Staatsministerin (Hessen)", None, False, "Bundesrat"),
     ("Ministerpräsidentin (Mecklenburg-Vorpommern)", None, False, "Bundesrat"),
     ("Wehrbeauftragter des Deutschen Bundestages", None, False, "Sonstige"),
     (None, None, False, "Sonstige")],
)  # fmt: skip
def test_speaker_group(role, fraction, government, group):
    assert speaker_group(role, fraction, government) == group


def test_ingest_groups(store):
    # a minister's speech counts for the government; her fraction is kept beside it
    bas = store.execute("SELECT speaker_group, member_fraction FROM speech WHERE id = 'ID219400100'").fetchone()
    assert tuple(bas) == ("Bundesregierung", "SPD")
    assert store.execute("SELECT count(*) FROM speech WHERE speaker_group IS NULL").fetchone()[0] == 0
    assert store.execute("SELECT fraction FROM person WHERE id = '11004006'").fetchone()[0] == "SPD"
    groups = dict(store.execute("SELECT number, originator_groups FROM drucksache"))
    assert json.loads(groups["21/6977"]) == ["Die Linke"] and json.loads(groups["21/7107"]) == []
    parties = dict(store.execute("SELECT party, fraction FROM party_fraction"))
    assert parties["CSU"] == "CDU/CSU" and parties["GRÜNE"] == "BÜNDNIS 90/DIE GRÜNEN"
