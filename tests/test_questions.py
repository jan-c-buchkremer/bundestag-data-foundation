"""question_activity: askers and answerers of Schriftliche and Mündliche Fragen from DIP's Aktivitäten."""

from datetime import date

import httpx
import pytest

from bdf import fetch_dip, raw


def _rows(store, vorgang_id):
    return {
        r["activity_type"]: dict(r)
        for r in store.execute("SELECT * FROM question_activity WHERE vorgang_id = ?", (vorgang_id,))
    }


def test_schriftliche_frage_has_asker_and_ressort(store):
    rows = _rows(store, "337712")  # Sammeldrucksache 21/7052, Fragen 211 and 212
    asker, answer = rows["Frage"], rows["Antwort"]
    assert asker["name"] == "Adam Balten, MdB, AfD" and asker["ressort"] is None
    assert (asker["document_kind"], asker["document_number"], asker["question_numbers"]) == (
        "Drucksache", "21/7052", "211, 212",
    )  # fmt: skip
    assert answer["ressort"] == "Bundesministerium für Gesundheit"
    assert asker["source_document_id"] == "BT-Drs. 21/7052"
    assert asker["source_url"] == f"https://search.dip.bundestag.de/api/v1/aktivitaet/{asker['id']}"


def test_muendliche_frage_from_the_protocol(store):
    rows = _rows(store, "322143")
    assert set(rows) == {"Frage", "Zusatzfrage", "Antwort"}
    assert rows["Antwort"]["ressort"] == "Auswärtiges Amt"
    assert (rows["Frage"]["document_kind"], rows["Frage"]["document_number"]) == ("Plenarprotokoll", "21/9")
    assert rows["Zusatzfrage"]["page"] == "683D"
    assert rows["Frage"]["source_document_id"] == "BT-PlPr. 21/9"


def test_ressort_keeps_the_commas_of_its_name(store):
    ressort = _rows(store, "322190")["Antwort"]["ressort"]
    assert ressort == "Bundesministerium für Bildung, Familie, Senioren, Frauen und Jugend"


def test_only_question_activities_are_kept(store):
    kinds = {r[0] for r in store.execute("SELECT DISTINCT question_type FROM question_activity")}
    assert kinds == {"Schriftliche Frage", "Mündliche Frage"}  # no Befragung, no speeches
    assert store.execute("SELECT COUNT(*) FROM question_activity WHERE vorgang_id = '322309'").fetchone()[0] == 0


def test_asker_is_linked_to_the_person(store):
    rows = _rows(store, "337461")  # Cansin Köktürk, a fixture MdB with a DIP person id
    assert rows["Frage"]["person_id"] == "11005505"


# --- fetch ---------------------------------------------------------------------------------


@pytest.fixture
def keyed(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_dip.time, "sleep", lambda s: None)
    monkeypatch.setenv("BDF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DIP_API_KEY", "test-key")


def test_protocol_activities_are_refetched_while_young(keyed):
    old, young = {"id": "1", "datum": "2025-06-04"}, {"id": "2", "datum": "2026-09-30"}
    raw.write_json(fetch_dip.dip_dir() / "aktivitaet" / "plenarprotokoll-1.json", "https://…", [{"id": "a"}])
    raw.write_json(fetch_dip.dip_dir() / "aktivitaet" / "plenarprotokoll-2.json", "https://…", [])
    requests = []

    def handler(request):
        requests.append(request.url)
        if request.url.path.endswith("/plenarprotokoll"):
            return httpx.Response(200, json={"documents": [old, young], "cursor": None})
        return httpx.Response(200, json={"documents": [], "cursor": None})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        fetch_dip.fetch_range(http, 21, date(2026, 10, 1), date(2026, 10, 4))
    asked = [u.params.get("f.plenarprotokoll") for u in requests if u.path.endswith("/aktivitaet")]
    assert asked == ["2"]  # the old protocol's file is kept, the young one asked again
