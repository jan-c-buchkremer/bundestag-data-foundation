# Test fixtures

Same layout as `data/raw/`; the tests copy this tree into a temporary data directory and
run the full ingest on it.

| Path | What | Origin |
|---|---|---|
| `bundestag/protocols/21/21094.xml` | Plenarprotokoll 21/94, **excerpt**: two agenda items, three `rede` elements (one with a Zwischenfrage). Marked as excerpt in an XML comment. | https://dserver.bundestag.de/btp/21/21094.xml, Deutscher Bundestag |
| `decisions/21090.xml` | Plenarprotokoll 21/90, **excerpt** for the decision parser: agenda items ZP 28, 29 and TOP 25 without their speeches and without the roll-call name lists. Outside the raw layout so the main ingest tests keep their counts; `test_parse_decisions.py` copies it in. | https://dserver.bundestag.de/btp/21/21090.xml, Deutscher Bundestag |
| `bundestag/stammdaten/MDB_STAMMDATEN.XML` | **Excerpt**: 11 MDB records (incl. two Beckers and historical namesakes) | https://www.bundestag.de/resource/blob/472878/MdB-Stammdaten.zip, Deutscher Bundestag |
| `bundestag/votes/20260710_7-xls.xlsx` | Roll-call vote 21/90/7 (Gebäudemodernisierungsgesetz), **unchanged** | https://www.bundestag.de/resource/blob/1194636/20260710_7-xls.xlsx, Deutscher Bundestag |
| `bundestag/votes/index.json` | the list row for that vote | bundestag.de vote list |
| `abgeordnetenwatch/wp21-*.json` | 8 real politician / mandate records; `wp21-sidejobs.json`: 6 real side jobs of four of them (recorded 2026-09-28) | abgeordnetenwatch.de API v2, CC0 1.0 |
| `bundeswahlleiterin/btw25/*.csv` | **Excerpts**: 9 rows of the elected candidates (the fixture MdBs, plus two namesakes who are not in the fixture Stammdaten) and all rows of Wahlkreise 14, 114 and 297 from `kerg2.csv`; preamble and header unchanged | https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/opendata.html, © Die Bundeswahlleiterin, Wiesbaden 2025, Datenlizenz Deutschland – Namensnennung 2.0 |
| `dip/**` | Recorded responses (2026-09-27), **trimmed**: 4 Drucksachen of 6–10 July 2026 — 21/6977 (Entschließungsantrag, Die Linke), 21/7107 (Beschlussempfehlung with Berichterstattung activities), 21/7052 (Schriftliche Fragen: 3 of 148 askers, 3 of 230 Vorgänge; `autoren_anzahl` is 0), 21/7009 (Beschlussempfehlung behind roll-call vote 21/90/7) — with their activities and Vorgänge; the one Vorgangsposition with the vote's Namentliche Abstimmung plus one other; the 13 WP21 persons matching the fixture Stammdaten or a Staatssekretär. | DIP API, Deutscher Bundestag/Bundesrat – DIP |
| `bundestag/biografien/page-000.html` | **Excerpt**: 7 of 639 cards of the MdB biography list, each card unchanged (six fixture MdBs, and Sanae Abdi, who is not in the fixture Stammdaten) | https://www.bundestag.de/ajax/filterlist/de/abgeordnete/1040594-1040594, Deutscher Bundestag |
| `bundestag/fotos/*.jpg` | Two portraits from those cards, **unchanged** (Bärbel Bas, Pascal Meiser); credits as on the cards | bundestag.de; rights of the named photographers |
| `wikidata/government.json` | Recorded SPARQL response (2026-09-28), **trimmed** to the rows of Merz, Bas, Hubig and Warken (two roles) | query.wikidata.org, CC0 1.0 |
| `wikidata/commons.json`, `wikidata/fotos/*.jpg` | Commons imageinfo of their P18 images, and one 864 px thumbnail (Stefanie Hubig) | Wikimedia Commons; photo by Sandro Halank, CC BY-SA 4.0 |

Every raw file has a `.meta.json` sidecar with `url` and `retrieved_at`, as `bdf fetch` writes it.
