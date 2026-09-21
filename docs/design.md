# Design

Scope: a weekly-updated store of Bundestag data for the current Wahlperiode (21),
built from three sources — bundestag.de Open Data, the DIP API, abgeordnetenwatch.de —
with a source pointer on every fact. See `landscape.md` for why these sources.

## Storage

- **SQLite**, one file `data/bundestag.sqlite`, Python stdlib `sqlite3`, WAL mode.
  Chosen over DuckDB because the workload is joins over small tables, a single
  writer once a week, and readers in other repos that only need `sqlite3`.
  FTS5 can be added later for full-text search; not needed for the acceptance test.
- **Raw files are kept on disk unchanged** under `data/raw/`, one folder per source.
  The store can always be rebuilt from `data/raw/` without network access.

```
data/
  raw/
    bundestag/
      stammdaten/MdB-Stammdaten.zip          # + extracted MDB_STAMMDATEN.XML
      protocols/21/21094.xml                 # dserver.bundestag.de/btp/21/21094.xml
      votes/20260710_7-xls.xlsx (+ .pdf)     # + votes/index.json (list rows: date, title, urls)
    dip/
      drucksache/2026-07-06_2026-07-10.json  # list responses, one file per fetched range
      aktivitaet/drucksache-<id>.json        # authors of one Drucksache
      vorgangsposition/2026-07-06_2026-07-10.json
      vorgang/drucksache-<id>.json           # all Vorgänge linked to one Drucksache
      person/wp21.json
    abgeordnetenwatch/
      wp21-mandates.json
      wp21-politicians.json
  bundestag.sqlite
```

Every raw file has a sidecar `<name>.meta.json` = `{"url", "retrieved_at"}` written by
`fetch`; `ingest` takes provenance from it.

## Identifiers

- `person.id` = the Bundestag MdB ID (`MDB/ID` in the Stammdaten, `redner/@id` in the
  protocol XML), as TEXT, e.g. `11004006`. Non-MdB speakers (ministers without a
  mandate, Bundesrat members, guests) also carry an id from the same namespace in the
  XML and get a `person` row with `is_mdb = 0`.
- `sitting.id` = `"<wp>/<nr>"`, e.g. `21/94` — the same string the Bundestag prints as
  "Plenarprotokoll 21/94".
- `speech.id` = the XML `rede/@id` (`ID219400100`), suffixed `-2`, `-3`… when one
  `rede` element contains several speakers (Zwischenfrage) and is split.
- `drucksache.id` / `vorgang.id` = DIP ids (TEXT); `drucksache.number` = `21/7300`.
- `roll_call_vote.id` = `"<wp>/<sitting>/<abstimmnr>"`, e.g. `21/90/7`.
- Cross-IDs live on `person`: `dip_person_id`, `aw_politician_id`, `wikidata_qid`.

## Provenance

Every fact table has three columns:

| column | meaning |
|---|---|
| `source_url` | the exact file or API URL the row was parsed from |
| `source_document_id` | the citable document, e.g. `BT-PlPr. 21/94`, `BT-Drs. 21/7300`, `NA 21/90/7`, `MDB_STAMMDATEN 2026-04-29`, `DIP aktivitaet 1493545`, `aw politician 79455` |
| `retrieved_at` | ISO timestamp of the download of the raw file |

Child rows that are parsed from the *same* raw file as their parent
(`speech_paragraph` ← `speech`, `individual_vote` ← `roll_call_vote`) inherit provenance
through the foreign key and do not repeat the three columns. Every other row carries them.

## Tables

Types are SQLite affinities. `*` = primary key. `→` = foreign key.

**person** `*id, first_name, last_name, name_prefix (Adel/Präfix), academic_title, birth_date, birth_place, gender, party, is_mdb, role (non-MdB speakers: rolle_lang), dip_person_id, aw_politician_id, wikidata_qid, source_url, source_document_id, retrieved_at`

**mandate** `*id, person_id →person, wahlperiode, from_date, to_date, mandate_type (Direktwahl/Landesliste), constituency_number, constituency_name, state, source_url, source_document_id, retrieved_at`

**membership** `*id, person_id →person, wahlperiode, kind (fraction | committee | other), name, role (FKT_LANG, e.g. Vorsitzende), from_date, to_date, source_url, source_document_id, retrieved_at`
— from Stammdaten `INSTITUTIONEN`; `kind` is derived from `INSART_LANG`.

**sitting** `*id, wahlperiode, number, date, start_time, end_time, xml_url, pdf_url, source_url, source_document_id, retrieved_at`

**agenda_item** `*id, sitting_id →sitting, position, top_id (XML top-id attribute), title, drucksache_numbers (JSON array of "21/7300"), source_url, source_document_id, retrieved_at`

**speech** `*id, sitting_id →sitting, agenda_item_id →agenda_item, position (order within sitting), person_id →person, speaker_name (as printed), speaker_role (rolle_lang or NULL), fraction (as printed, normalised), text (clean speech text: paragraphs of kind text only, joined by blank lines), source_url, source_document_id, retrieved_at`

**speech_paragraph** `*id, speech_id →speech, position, kind (text | comment | chair | procedural), text`
— `text` = the speaker's words (`p` classes J, J_1, O, …); `comment` = `<kommentar>` (applause, interjections); `chair` = presidency remarks inside the `rede` (`<name>Vizepräsident…</name>` and the paragraphs until the speaker resumes); `procedural` = `T_*` classes.

**drucksache** `*id (DIP), number, wahlperiode, type (drucksachetyp), title, date, pdf_url, publisher (herausgeber), originators (JSON of urheber titles), author_count, source_url, source_document_id, retrieved_at`

**drucksache_author** `*id, drucksache_id →drucksache, dip_person_id, person_id →person (NULL until resolved), name (DIP titel), activity_type (aktivitaetsart), source_url, source_document_id, retrieved_at`
— from `/aktivitaet?f.drucksache=<id>`; DIP's `autoren_anzeige` alone is truncated to 4.

**vorgang** `*id (DIP), wahlperiode, type (vorgangstyp), title, status (beratungsstand), subjects (JSON sachgebiet), initiators (JSON initiative), source_url, source_document_id, retrieved_at`

**vorgang_drucksache** `vorgang_id →vorgang, drucksache_id →drucksache` (composite PK)

**roll_call_vote** `*id, sitting_id →sitting, number (Abstimmnr), date, title (from the bundestag.de list), drucksache_number (NULL until linked), vorgang_id →vorgang (NULL until linked), link_method (dip_beschluss | title_regex | manual | NULL), yes, no, abstain, invalid, absent (totals computed from individual_vote), xlsx_url, pdf_url, source_url, source_document_id, retrieved_at`

**individual_vote** `*id, vote_id →roll_call_vote, person_id →person (NULL if unmatched), last_name, first_name, fraction, vote (yes | no | abstain | invalid | absent)`

Fraction majority per vote is a query, not a column:
`SELECT fraction, vote, COUNT(*) … GROUP BY fraction, vote`.

No separate `fraction` table: fraction is a normalised string (`CDU/CSU`, `SPD`, `AfD`,
`BÜNDNIS 90/DIE GRÜNEN`, `Die Linke`, `fraktionslos`) with one normalisation function
shared by the XML, XLSX and Stammdaten parsers. A table would add a join and nothing else.

## Entity linking

1. **Speech → person**: `redner/@id` directly. Unknown ids (non-MdB speakers) create a
   `person` row from the XML name block (`is_mdb = 0`, role from `rolle_lang`).
2. **Vote row → person**: `(last_name, first_name)` against persons holding a mandate in
   that Wahlperiode, then against all known persons (Nachrücker without a mandate row yet);
   Ortszusatz "(Altötting)" and name prefixes ("dos", "von") are stripped; first-name
   fallback to the first given name; a small alias table in code for known spelling
   differences. Unmatched rows keep `person_id NULL` and are reported by `ingest`.
3. **DIP person → person**: `/person?f.wahlperiode=21` list, matched on
   `(nachname, vorname)` after normalisation with the same matcher as the votes.
   Unmatched DIP persons (mostly non-MdBs) are counted; `drucksache_author.person_id` stays NULL.
4. **abgeordnetenwatch politician → person**: `(last_name, first_name, year_of_birth)`
   against Stammdaten; `ext_id_bundestagsverwaltung` is only compared and disagreements
   are reported (it is wrong for ~9 % of WP21 members, see `landscape.md` §1.6).
5. **Roll-call vote → Drucksache/Vorgang**: DIP `vorgangsposition` on the sitting date
   whose `beschlussfassung.abstimmungsart = "Namentliche Abstimmung"`; ordered by
   protocol page, paired with the votes of that sitting ordered by `Abstimmnr` when the
   counts match; otherwise a Drucksache number found by regex in the vote title;
   otherwise unlinked (reported).

## Incremental updates

- Unit of work is a date range (a sitting week). `fetch` downloads anything in the range
  that is not already on disk (files are immutable; a re-fetch overwrites only with
  `--force`). `ingest` upserts by primary key, so re-running is safe and idempotent.
- Stammdaten and DIP persons are re-fetched whole (small) and upserted.
- Corrections to protocols are rare; `fetch --force` + `ingest` handles them.

## CLI

```
bdf fetch stammdaten
bdf fetch protocols --wp 21 --from 88 --to 90
bdf fetch votes --from 2026-07-06 --to 2026-07-10
bdf fetch dip --from 2026-07-06 --to 2026-07-10     # drucksachen, authors, vorgänge, vorgangspositionen, persons
bdf fetch aw --wp 21
bdf ingest                                          # everything under data/raw → sqlite
bdf query speeches   --person 11004006 --from … --to …
bdf query votes      --person 11004006 --from … --to …
bdf query drucksachen --person 11004006 --from … --to …
bdf query corpus     --from … --to …                # JSONL: one clean speech per line with speaker id, fraction, date, source
```

Every query prints the source pointer on each row.
