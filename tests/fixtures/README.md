# Test fixtures

Same layout as `data/raw/`; the tests copy this tree into a temporary data directory and
run the full ingest on it.

| Path | What | Origin |
|---|---|---|
| `bundestag/protocols/21/21094.xml` | Plenarprotokoll 21/94, **excerpt**: two agenda items, three `rede` elements (one with a Zwischenfrage). Marked as excerpt in an XML comment. | https://dserver.bundestag.de/btp/21/21094.xml, Deutscher Bundestag |
| `bundestag/stammdaten/MDB_STAMMDATEN.XML` | **Excerpt**: 11 MDB records (incl. two Beckers and historical namesakes) | https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip, Deutscher Bundestag |
| `bundestag/votes/20260710_7-xls.xlsx` | Roll-call vote 21/90/7 (Gebäudemodernisierungsgesetz), **unchanged** | https://www.bundestag.de/resource/blob/1194636/20260710_7-xls.xlsx, Deutscher Bundestag |
| `bundestag/votes/index.json` | the list row for that vote | bundestag.de vote list |
| `abgeordnetenwatch/wp21-*.json` | 8 real politician / mandate records | abgeordnetenwatch.de API v2, CC0 1.0 |
| `dip/**` | **Synthetic** responses written to the DIP OpenAPI 3.0.1 spec (v1.5): 2 Drucksachen, 2 author activities, 2 Vorgänge, 2 Vorgangspositionen, 6 persons. Replace with recorded responses once a DIP key is available. | — |

Every raw file has a `.meta.json` sidecar with `url` and `retrieved_at`, as `bdf fetch` writes it.
