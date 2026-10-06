# Design

Scope: a weekly-updated store of Bundestag data for the current Wahlperiode (21),
built from bundestag.de Open Data, the DIP API and abgeordnetenwatch.de, plus the Bundeswahlleiterin's results,
bundestag.de portraits and the Wikidata government roster —
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
      drucksachen/21/2101095.pdf             # dserver.bundestag.de/btd/21/010/2101095.pdf: the answers to Kleine
                                             # and Große Anfragen (+ .answer.json, the parse) and the
                                             # Sammeldrucksachen of Schriftliche Fragen (+ .schriftliche.json) the
                                             # DIP data lists
      votes/20260710_7-xls.xlsx (+ .pdf)     # + votes/index.json (list rows: date, title, urls)
    dip/
      drucksache/2026-07-06_2026-07-10.json  # list responses, one file per fetched range
      aktivitaet/drucksache-<id>.json        # authors of one Drucksache
      plenarprotokoll/wp21.json              # all BT Plenarprotokolle of the Wahlperiode
      aktivitaet/plenarprotokoll-<id>.json   # Aktivitäten of one protocol (Mündliche Fragen, speeches); fetched
                                             # again while the protocol is younger than 180 days
      vorgangsposition/2026-07-06_2026-07-10.json  # all BT Vorgangspositionen dated in the range
      vorgangsposition_other/2025-03-25_2026-07-10-BR.json  # BR/BV/EK Vorgangspositionen, one file per
                                                              # zuordnung and range (Bundesrat steps: 1./2.
                                                              # Durchgang, Zustimmung, Einspruch, Vermittlungs-
                                                              # ausschuss, …)
      vorgang/drucksache-<id>.json           # all Vorgänge linked to one Drucksache
      person/wp21.json
    abgeordnetenwatch/
      wp21-mandates.json
      wp21-politicians.json
    bundeswahlleiterin/
      btw25/btw25_gewaehlte_utf8.zip         # + extracted btw25_gewaehlte_utf8.csv: the elected
      btw25/kerg2.csv                        # results per Wahlkreis, party and vote
      btw25/btw25_nachfolger.pdf             # Mandatsnachfolger, downloaded again on every run
    bundestag/biografien/page-000.html …     # the MdB card list, 12 cards per page, replaced on every fetch
    bundestag/fotos/<image id>.jpg           # portraits as downloaded (864×1152), never re-downloaded
    wikidata/
      positions.json                         # SPARQL: position items under Bundesminister / PStS / beamteter StS
      government.json                        # SPARQL: P39 statements since 2025-05-06 with dates, department, person
      commons.json                           # Commons imageinfo (thumbnail URL, author, licence) of the P18 images
      fotos/<Commons file name>              # 864 px thumbnails
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
- Placeholder person ids (a Wikidata QID for a government member without a match, `pdf-<name>` for a speaker read
  from a PDF) disappear once the person is matched. **person_alias** `*alias_id, person_id →person, recorded_at`
  keeps where they went, so a consumer can redirect a page address built from the old id: written when
  `ingest_government` drops a matched QID row and when `retire_pdf_speakers` (the last ingest step) drops a
  `pdf-` row nothing refers to any more, aliased to the one other person of that name. Rows are never removed.

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

## Schema contract (from v0.2.0)

What a consumer (the cards, the landscape, users of the open-data export) may rely on. The tables are described
in full under "Tables"; this section says what stays. The foundation releases as `v0.MINOR.PATCH` like the other
repos (`docs/release.md`), so the version number alone does not say what is stable: this contract and the CHANGELOG
do.

**Promised.** Every table and column in the schema (`bdf/db.py`, the export's `datapackage.json`) keeps its name, its
type and its meaning, and every id keeps its form and keeps naming the same thing, except where "Ids" below says
when one changes. Rows carry their provenance (`source_url`, `source_document_id`, `retrieved_at`, or the parent's).

- **Additions** (a minor or patch release): new tables, new columns, new values of an enumerated column
  (`speech.kind`, `decision_vorgang.via`, …), more rows because a parser finds more, rows corrected because a parser
  reads better. The CHANGELOG names each one. Consumers must not fail on a column or value they do not know.
- **Breaking changes** (only in a minor release): a table or column removed or renamed, a meaning changed, an id
  form changed. The release's CHANGELOG entry lists them first, under "Breaking", and says what a consumer has to
  change; where it can, the old column stays for one more release.
- **Not covered:** the raw files under `data/raw`, the CLI's printed output, `data/health/*.json`, the order of rows
  (sort by a column), and the facts themselves: values follow the sources, and derived columns
  (`person.fraction`, `speech.speaker_group`, `decision_vorgang`, …) are recomputed on every ingest.

### Ids

| Id | Form | Changes when |
|---|---|---|
| `person.id` | Bundestag MdB id, `11004006` | never; a placeholder (`Q…` Wikidata QID, `pdf-<name>`) is replaced by the real id once matched, and `person_alias` records where it went |
| `sitting.id` | `21/94` | never |
| `agenda_item.id` | `<sitting>/<position>` | the final version of a preliminary protocol replaces it (items after the XML's end are counted anew) |
| `agenda_sub_item.id` | `<agenda_item_id>/<label>`, `21/96/6/41b` | with its agenda item |
| `speech.id` | XML `rede/@id` (`ID219400100`, parts `-2`, `-3`); Fragestunde turns `<agenda_item_id>/f<n>`; from a PDF part `ID21014pdf001` | PDF-part ids and Fragestunde numbers of a preliminary protocol change once with its final version |
| `decision.id` | roll call: the vote's id `21/90/7` (`21/90/n<k>` before the vote list is published); show of hands: `<sitting>/p<paragraph>` (`-2`, `-3` for further votes asked in one paragraph) | the protocol text changes (final version of a preliminary protocol); not when the parser finds or drops other decisions |
| `roll_call_vote.id` | `<sitting>/<Abstimmnr>`, `21/90/7` | never |
| `drucksache.id`, `vorgang.id`, `vorgang_position.id`, `question_activity.id` | DIP ids | never (DIP's) |
| `vorgang_referral` (`position_id, committee`) | DIP position id and DIP's committee name | when DIP renames the committee |
| `mandate.id`, `membership.id` | `<person>/<wp>`, `<person>/<wp>/<k>` | `membership` `k` counts the Stammdaten entries of a person and Wahlperiode, so a new Stammdaten file can renumber them: join on the columns, not the id |
| `interjection.id`, `speech_paragraph.id`, `agenda_item_paragraph.id` | `<speech or item id>/<position>/…` | with the speech or item |
| `question_turn.speech_id`, `thread_id` | speech ids | with the speech |
| `question_text.id`, `question_table.id` | `<Drucksache of the question>/<number>/<part>`: Mündliche Fragen `21/1949/6/antwort` (the Fragen-Drucksache); Anfragen `21/1026/3a/frage`, `21/1026/vorbemerkung_fragesteller`, `21/1026/1/anlage` (the Anfrage); tables `…/<k>` | never (the Drucksache's numbering); an annex's number counts the annexes of the answer |
| `question_parse.vorgang_id` | DIP Vorgang id | never |
| `constituency.id`, `constituency_result.id`, `election_candidacy.id`, `mandate_successor.id` | `btw25/…` | never (the files are final; `mandate_successor` uses the list's running number) |
| `government_role.id` | Wikidata statement id, else `<source_kind>:<person>:<kind>:<department key>` | when a better source takes the row over (`source_kind`) |
| `side_job.id`, `aw_profile.aw_politician_id` | abgeordnetenwatch ids | never (aw's) |

Page addresses built from ids that can change should accept the old one too: `person_alias` for persons; for the
rest, a changed id comes with a re-ingest of the same sitting, so a consumer can map old to new by sitting and text.

### What NULL means

NULL is never "zero" or "false". Columns not listed are NOT NULL, or declared nullable but filled in every row of
the live data on 2026-10-04 (`sitting.start_time`, `speech.agenda_item_id`, `speech.speaker_group`,
`decision.agenda_item_id`, `drucksache.type`, `vorgang.type`, `roll_call_vote.sitting_id`, …): a consumer may expect
them filled but must not fail on a NULL.

| Column | NULL means |
|---|---|
| `person.name_prefix`, `academic_title`, `birth_date`, `birth_place`, `gender`, `party` | not in the source (protocol-only speakers have names only) |
| `person.role` | an MdB; set for non-MdB speakers (their printed role) |
| `person.dip_person_id`, `aw_politician_id`, `wikidata_qid` | no match in that source |
| `person.fraction` | not a member in the newest Wahlperiode |
| `mandate.to_date` | the mandate is current |
| `mandate.mandate_type`, `constituency_number`, `constituency_name` | not in the Stammdaten (list mandates have no Wahlkreis) |
| `membership.role` | a plain member; `from_date`, `to_date`: not in the Stammdaten (only Wahlperioden 1–17 lack a start), resp. still a member |
| `sitting.final_announced` | the protocol is final; `final_fetched_at`: still preliminary; `last_page`: no PDF part read |
| `agenda_item.title` | the item has no title lines of its own (budget Einzelpläne continuing an item: 66) |
| `agenda_item.kind` | a regular item (not Befragung, Fragestunde or Aktuelle Stunde) |
| `agenda_item_vorlage.sub_item_id` | listed on the item itself; `vorgang_id`: no single Vorgang (none, or several) |
| `speech.speaker_role` | the speaker spoke as a member; `fraction`: no fraction printed (government, Bundesrat, guests); `member_fraction`: not a member that day |
| `speech.sub_item_id` | the item has no sub-items |
| `speech.interruption`, `interruption_start` | the main speaker's part, or a Befragung/Fragestunde turn |
| `question_turn.thread_id` | an opening statement (`einleitung`), which belongs to no question; `vorgang_id`: a Befragung turn, or a Fragestunde question DIP has no Vorgang for |
| `question_text.vorgang_id` | DIP has no Vorgang for the question (33 Mündliche Fragen); `drucksache_id`: the Drucksache is not fetched; `number`: a preliminary remark; `answerer_person_id`: a frage row, an answer to an Anfrage (a ministry answers), or DIP names no person (Mündliche and Schriftliche Fragen take it from DIP's Antwort activity); `answer_date`: a frage row or the askers' preliminary remark; `thread_id`: not answered in the Fragestunde; `name`: not printed, a frage row of an Anfrage |
| `question_parse.source_document_id` | the Anfrage is not answered |
| `interjection.fraction`, `person_id`, `name`, `text` | not applicable to the actor or kind (applause has no text; a fraction's applause no person); `to_person_id`, `to_name`: addressed to the speaker |
| `decision.drucksache_number` | the chair named none (an Einzelplan, an immunity matter); `result`: not read out in the protocol; `roll_call_vote_id`: show of hands, or the vote list is not out yet; `sub_item_id`: see the table; `vorgang_id`: not exactly one Vorgang; `dip_position_id`, `dip_result`: no single DIP step names it |
| `roll_call_vote.drucksache_number`, `vorgang_id`, `link_method`, `agenda_item_id` | not linked |
| `drucksache_author.person_id`, `question_activity.person_id`, `individual_vote.person_id`, `aw_profile.person_id`, `side_job.person_id`, `election_candidacy.person_id`, `mandate_successor.person_id`, `predecessor_person_id`, `government_role.person_id` | not matched to a person (mostly non-MdBs; Schnurrbusch declined his seat) |
| `question_activity.ressort` | a Frage or Zusatzfrage row (the Ressort is on the Antwort); `question_numbers`: a protocol row; `page`: a Drucksache row |
| `drucksache.author_count` | DIP gives none and no activities were fetched |
| `vorgang.status`, `verkuendung`, `inkrafttreten` | DIP has none (not every Vorgang ends in a law) |
| `vorgang_position.document_type`, `pages`, `ressort`, `decisions` | DIP has none for that step (`pages` only on protocol steps) |
| `vorgang_referral.committee_short`, `kind` | DIP gives no `ausschuss_kuerzel` resp. `ueberweisungsart` for that committee (most referrals have no `ueberweisungsart`) |
| `constituency.seat_party` | the winner had no Zweitstimmendeckung |
| `election_candidacy.constituency_number`, `first_vote_percent`, `list_state`, `list_position` | did not stand there / not a constituency winner / not on a list |
| `government_role.to_date` | in office (protocol rows: never NULL, see the table); `wikidata_qid`, `department`: not known |
| `person_photo.bio_url` | a Commons portrait |
| `aw_profile.questions_answered` | aw publishes none |
| `side_job.*` (optional columns) | not published by abgeordnetenwatch for that entry |

### Known gaps

Measured on the live store's data on 2026-10-04 (`bdf health` prints the current counts):

- **Beratungen without an agenda item:** 23, none a parser miss; causes per case under "Preliminary protocols".
- **Decisions against DIP** (`bdf query decision-check`): 1 result DIP records differently (21/34, a Wahlvorschlag
  the chair announced as rejected, DIP as adopted) and 3 DIP decisions without a row (a Kommission proposal in 21/14
  and two Sammelübersichten in 21/18 and 21/65). 3 decisions have no result because the chair did not read one out.
- **Preliminary protocols:** 17; their end comes from the final PDF until bundestag.de serves the final XML.
- **Vorgänge:** fetched only through WP 21 Drucksachen, so 7 Vorgänge that DIP places in a protocol (WP 20 reports,
  procedure) have no row; 188 Drucksachen on agenda items have no single Vorgang.
- **Persons:** 4 askers of Fragen and 30 DIP persons on Drucksachen are not matched (DIP person matching); 1
  `pdf-` placeholder (a Land minister).
- **Lagging sources:** the Stammdaten file dates from 2026-04-29, so Breilmann, Glaser, Naser and Zschau, who vote in
  WP 21, have no WP 21 `mandate`; the Bundeswahlleiterin's successor list (changed 2026-06-23) gives the Land of the
  first three, not yet Zschau's.
- **Fragen:** 40 Mündliche Fragen have no Ressort (37 not answered, 2 withdrawn).
- **Known wrong value:** decision `21/14/p207` (the Greens' Faire-Mieten-Gesetz, 21/222) has the subject of the
  coalition's Mietpreisbremse bill, taken from an earlier introduction.
- **Government roles from protocols** are evidence dates, not appointment dates (see `government_role`).
- **Question turns** (`question_turn`) follow the presidency's words, which it chooses freely; where they say nothing
  clear, the turn's structure decides (see "Question turns"). Checked by hand on four random samples of member turns
  (230 in all); the errors in the first three shaped the rules, the last sample (50 turns, final rules) had none,
  which still allows an error rate of a few percent. The doubtful cases are members called by fraction or name alone:
  a question of their own, or a follow-up.
- **Schriftliche Fragen** (`question_text`): 104 of 9,862 have no DIP Vorgang (DIP lacks them: 24 in 21/7670, 76 in
  21/7980, one each in 21/4006, 21/4573, 21/4657 and 21/7311); 10 DIP Vorgänge keep a question a second time and
  have no texts (`question_parse` `failed`).
- **Answers to Anfragen** (`question_text`): tables without ruling, or ruled only in their head, are not read
  (`{"extracted": false, "page": n}`; 108 of 1,542 tables in a sample of 149 answers, 81 of them in one statistics
  annex). Footnotes and notes under tables are left out. The answers are read from their PDFs, fetched the night
  after DIP lists them; until then `question_parse` says `failed`.
- **Question texts** (`question_text`): 33 of the 1,613 Mündliche Fragen the protocols print have no DIP Vorgang;
  128 written answers have no `answerer_person_id` (DIP names no person). Questions answered under Nr. 9 Satz 2 of
  the Richtlinien für die Fragestunde have no text in the protocol and no row; the Fragen-Drucksachen, not read yet,
  have it.
- **Committee referrals** (`vorgang_referral`) name the committee as DIP spells it, which differs from the
  Stammdaten's `membership.name`; the foundation does not match the two. A referral exists only where DIP records it
  on a Vorgangsposition.

## Tables

Types are SQLite affinities. `*` = primary key. `→` = foreign key.

**person** `*id, first_name, last_name, name_prefix (Adel/Präfix), academic_title, birth_date, birth_place, gender, party, is_mdb, role (non-MdB speakers: rolle_lang), dip_person_id, aw_politician_id, wikidata_qid, source_url, source_document_id, retrieved_at`

**mandate** `*id, person_id →person, wahlperiode, from_date, to_date, mandate_type (Direktwahl/Landesliste), constituency_number, constituency_name, state, source_url, source_document_id, retrieved_at`

**membership** `*id, person_id →person, wahlperiode, kind (fraction | committee | other), name, role (FKT_LANG, e.g. Vorsitzende), from_date, to_date, source_url, source_document_id, retrieved_at`
— from Stammdaten `INSTITUTIONEN`; `kind` is derived from `INSART_LANG`.

**sitting** `*id, wahlperiode, number, date, start_time, end_time, xml_url, pdf_url, source_url, source_document_id, retrieved_at, preliminary (0 | 1), final_announced, final_fetched_at, first_page, last_page`
— `preliminary = 1`: the XML on disk is the preliminary version (below, "Preliminary protocols"); `final_announced` is the date its note gives for the final version, `final_fetched_at` the `retrieved_at` of the XML once it is final (NULL while preliminary). `first_page` is `start-seitennr`. `last_page` is set only for a preliminary sitting whose PDF part was read: the Druckseite the XML's text ends on, found in the final PDF; the agenda items, paragraphs and speeches after it come from the PDF and have it as their `source_url` (`source_document_id` "BT-PlPr. 21/31 (PDF)").

**agenda_item** `*id, sitting_id →sitting, position, top_id (XML top-id attribute), title, drucksache_numbers (JSON array of "21/7300"), source_url, source_document_id, retrieved_at, no_debate (0 | 1, default 0)`
— `no_debate = 1`: the chair said that no Aussprache is provided ("zu denen keine Aussprache vorgesehen ist", "ohne Debatte", "Eine Aussprache ist nicht vorgesehen"). For a block item (below) it means the block was called up that way. Speeches of the item stay as they are.

**speech** `*id, sitting_id →sitting, agenda_item_id →agenda_item, position (order within sitting), person_id →person, speaker_name (as printed), speaker_role (rolle_lang or NULL), fraction (as printed, normalised), text (clean speech text: paragraphs of kind text only, joined by blank lines), source_url, source_document_id, retrieved_at, kind (rede | fragestunde, default 'rede'), sub_item_id →agenda_sub_item`
— `kind = 'fragestunde'`: a question, answer or Nachfrage from a Fragestunde (below), id `"<agenda_item_id>/f<n>"`. Shown like any other speech, but left out of speech counts and speaking shares by callers (it is not a debate contribution).

**speech_paragraph** `*id, speech_id →speech, position, kind (text | comment | chair | procedural), text`
— `text` = the speaker's words (`p` classes J, J_1, O, …); `comment` = `<kommentar>` (applause, interjections); `chair` = presidency remarks inside the `rede` (`<name>Vizepräsident…</name>` and the paragraphs until the speaker resumes); `procedural` = `T_*` classes.

**drucksache** `*id (DIP), number, wahlperiode, type (drucksachetyp), title, date, pdf_url, publisher (herausgeber), originators (JSON of urheber titles), author_count (DIP `autoren_anzahl`; the number of distinct persons in the fetched activities where the two disagree), source_url, source_document_id, retrieved_at`

**drucksache_author** `*id, drucksache_id →drucksache, dip_person_id, person_id →person (NULL until resolved), name (DIP titel), activity_type (aktivitaetsart: Antrag, Kleine Anfrage, Große Anfrage, Entschließungsantrag, Änderungsantrag, Gesetzentwurf, Frage count as authorship; Berichterstattung and Antwort do not), source_url, source_document_id, retrieved_at`
— from `/aktivitaet?f.drucksache=<id>`; DIP's `autoren_anzeige` alone is truncated to 4. One row per person and Drucksache: a member with three questions in one Sammeldrucksache has one row; the single questions are in `question_activity`.

**question_activity** `*id (DIP Aktivität), vorgang_id (the single question), question_type (Schriftliche Frage | Mündliche Frage), activity_type (Frage | Zusatzfrage | Antwort), dip_person_id, person_id →person (NULL until resolved), name (DIP titel), ressort (Antwort only), document_kind (Drucksache | Plenarprotokoll), document_number, question_numbers (Drucksache: frage_nummer, "93, 94"), page (Plenarprotokoll: "683D"), source_url, source_document_id, retrieved_at`
— who asked and who answered each Frage: the Aktivitäten of the Sammeldrucksachen (types Schriftliche Fragen and Fragen) and of every Plenarprotokoll (`/aktivitaet?f.plenarprotokoll=<id>`), kept where `vorgangsbezug` names exactly one Vorgang of a question type. The asker of a Frage is its `Frage` row, its Ressort the `ressort` of its `Antwort` row: the part of the answerer's title after name and function ("Ulrich Lange, Parl. Staatssekr., Bundesministerium für Verkehr"), as DIP spells it at the time (the ministries before May 2025 have their old names). DIP's Vorgangspositionen of Fragen carry no `ressort`. A Mündliche Frage answered in writing has its Frage and Antwort on the protocol (the Anlage); one asked only on the Fragen-Drucksache has no Antwort. Live store, 2026-10-04: all 9,038 Schriftliche and 1,624 Mündliche Fragen have one asker; 9,037 and 1,584 a Ressort (the rest not answered or withdrawn); 4 askers (191 activities) have no `person_id` because their DIP person is not matched.

**vorgang** `*id (DIP), wahlperiode, type (vorgangstyp), title, status (beratungsstand), subjects (JSON sachgebiet), initiators (JSON initiative), verkuendung (JSON DIP verkuendung objects: BGBl reference, Ausfertigungs-/Verkündungsdatum, or NULL), inkrafttreten (JSON array of {datum, erlaeuterung}, or NULL), source_url, source_document_id, retrieved_at`
— Verkündung/Ausfertigung is not a vorgangsposition step under any zuordnung (checked against the whole BT-only Wahlperiode so far: no "Verkündung"/"Ausfertigung" position exists); DIP carries it only on the Vorgang record itself.

**vorgang_drucksache** `vorgang_id →vorgang, drucksache_id →drucksache` (composite PK)

**vorgang_position** `*id (DIP), vorgang_id (DIP; not every Vorgang is in vorgang), date, position (vorgangsposition: "Gesetzentwurf", "1. Beratung", "Antwort", …), chamber (zuordnung), document_kind (Drucksache | Plenarprotokoll), document_number, document_type (drucksachetyp), pdf_url, pages ("1234-1236", protocols), originators (JSON urheber titles), ressort (JSON [{titel, federfuehrend}] or NULL), decisions (JSON beschlussfassung as in DIP, or NULL), source_url, source_document_id ("DIP Vorgangsposition <id>"), retrieved_at`
— one row per step of a Vorgang, from the same date-range lists as the vote linking (deduplicated by id, latest copy wins). `chamber` (zuordnung) is BT, BR, BV or EK: BT positions come from the main range file, BR/BV/EK positions from the separate `vorgangsposition_other` files (one per zuordnung and range), so a step such as "1. Durchgang", "Zustimmung", "Kein Einspruch eingelegt" or "Anrufung des Vermittlungsausschusses" is a BR row like any BT row. Vote linking (`_link_votes_to_dip`) still reads BT positions only.

**vorgang_referral** `position_id →vorgang_position, vorgang_id (DIP), committee (ueberweisung.ausschuss as DIP names it), committee_short (ausschuss_kuerzel, "EU"), lead (1 = federführend, 0 = mitberatend; from federfuehrung), kind (ueberweisungsart or NULL), source_url, source_document_id ("DIP Vorgangsposition <id>"), retrieved_at` (PK `position_id, committee`)
— the committees a Vorlage was referred to: one row per entry of a position's `ueberweisung` list, from the same raw files and in the same pass as `vorgang_position` (BT and BR/BV/EK alike), deleted and written anew on every ingest. A position without `ueberweisung` has no row; a committee listed twice on one position is one row, lead if either entry says so. `committee` is DIP's name, not matched to `membership` (DIP and the Stammdaten name committees differently; a consumer keeps the alias map). A Vorgang can have referrals on several positions (a re-referral, Bundesrat committees on BR positions): pick by `vorgang_position.chamber` and `date`.

**roll_call_vote** `*id, sitting_id →sitting, number (Abstimmnr), date, title (from the bundestag.de list), drucksache_number (NULL until linked), vorgang_id →vorgang (NULL until linked), link_method (dip_beschluss | title_regex | manual | NULL), yes, no, abstain, invalid, absent (totals computed from individual_vote), xlsx_url, pdf_url, source_url, source_document_id, retrieved_at, agenda_item_id →agenda_item (see Chair text and decisions)`

**individual_vote** `*id, vote_id →roll_call_vote, person_id →person (NULL if unmatched), last_name, first_name, fraction, vote (yes | no | abstain | invalid | absent)`

Fraction majority per vote is a query, not a column:
`SELECT fraction, vote, COUNT(*) … GROUP BY fraction, vote`.

No separate `fraction` table: fraction is a normalised string (`CDU/CSU`, `SPD`, `AfD`,
`BÜNDNIS 90/DIE GRÜNEN`, `Die Linke`, `fraktionslos`) with one normalisation function
shared by the XML, XLSX and Stammdaten parsers. A table would add a join and nothing else.

### Fraction and speaker group

Derived columns, recomputed in full by `ingest_groups` at the end of every ingest, so that the consumers (cards,
landscape, the export's users) don't each work them out again in their own way. The vocabulary is in `bdf/names.py`
and nowhere else.

- **`speech.speaker_group`**: who a speech counts for. A speech given in a federal government office (the printed
  role parses with `government.parse_role`, or "Beauftragte der Bundesregierung …") counts for the
  **Bundesregierung**, whatever the speaker's fraction; a Land office ("Staatsministerin (Hessen)") for the
  **Bundesrat**; otherwise the printed fraction (`speech.fraction`), else **Sonstige** (the Wehrbeauftragte).
  The chair's words are not speeches, so the Präsidium never appears.
- **`speech.member_fraction`**: the speaker's fraction on the sitting day from `membership` (for a Nachrücker not
  yet in the Stammdaten, the printed one), also when the speech counts for the government. NULL for non-members.
- **`person.fraction`**: the fraction in the newest Wahlperiode (the open membership, else the last one; without
  one, as for a Nachrücker not yet in the Stammdaten, the latest one printed in protocols and vote lists, else
  "fraktionslos"). NULL for everyone else. *The* current fraction for consumers.
- **`drucksache.originator_groups`**: JSON array of the fractions and "Bundesregierung" among the DIP Urheber
  ("Fraktion der SPD" → SPD, "Gruppe …" likewise, a ministry → Bundesregierung); committees, the Bundesrat and the
  President are not groups.
- **`party_fraction`** `*party, fraction`: every party name in `person.party`, `election_candidacy.party`,
  `constituency.seat_party` and `constituency_result.party` that belongs to a fraction ("CSU" → CDU/CSU, "GRÜNE"
  → BÜNDNIS 90/DIE GRÜNEN, "DIE LINKE." → Die Linke), so a consumer joins instead of keeping a map. Parties without
  a fraction (SSW) have no row.

### Speech parts

Also derived, by `ingest_speech_parts` after `ingest_groups`:

- **`agenda_item.kind`**: `befragung` ("Befragung der Bundesregierung"), `fragestunde` (by title, or an item with
  Fragestunde speeches), `aktuelle_stunde`; NULL for every other item.
- **`speech.rede_id`**: the speech a part belongs to, the id without its "-2", "-3" … (a Fragestunde turn is its own).
- **`speech.interruption`**: for a part by someone other than the rede's first speaker, `kurzintervention` when the
  chair's words among the last six paragraphs of the part before it announce one ("Kurzintervention",
  "Zwischenbemerkung"), else `zwischenfrage`. The same person interrupting again counts as a new interruption only
  after at least 30 words of the main speaker (`NEW_INTERRUPTION_AFTER`), so "Gestatten Sie …? – Bitte." between
  two parts of one question does not split it; a part right after another interrupter takes that person's earlier
  kind, else `zwischenfrage`. NULL for the main speaker's parts and for every part in a Befragung or Fragestunde,
  where each question and answer is a turn of its own.
- **`speech.interruption_start`**: the first part of the interruption a part belongs to, so a question over two
  parts counts once: count distinct `interruption_start` values, not parts.

### Question turns

**question_turn** `*speech_id →speech, role (einleitung | frage | antwort | nachfrage | zusatzfrage), thread_id →speech (the question the turn belongs to: Befragung: its frage, a frage's own id, NULL for an einleitung; Fragestunde: the first turn after the call), vorgang_id →vorgang (Fragestunde: the DIP Mündliche Frage; NULL in the Befragung)`

One row per turn of a Befragung der Bundesregierung or a Fragestunde, read from the parsed protocol while the
presidency's words between two turns are still in order (`bdf/parse_question_turns.py`; the store keeps no position for
agenda paragraphs between speeches). Replaced with the sitting's speeches on every ingest.

**Fragestunde.** The presidency calls each question and reads it out, so the question is a `question_text` row, not a
turn. A turn belongs to the last question called before it: the government's turns are its `antwort`, the asker's (the
member the call names) a `nachfrage`, anyone else's a `zusatzfrage`; `thread_id` is the first turn after the call, and
`question_text.thread_id` points to it. `vorgang_id` is set with the question's (see "Question texts"). Measured on
2026-10-06 (25 Fragestunden): 1,536 turns on 170 questions, 868 answers, 355 Nachfragen, 313 Zusatzfragen.

**Befragung.**

- A government speaker gives an `einleitung` before the first question, an `antwort` after it; the answer belongs to
  the open question. Government means `speech.speaker_group` Bundesregierung, or a person who spoke in a government
  office earlier in the same Befragung (21/82 prints the minister answering as "Alois Rainer (CDU/CSU)").
- A member's turn is a new question (`frage`) when the presidency calls one ("nächste Hauptfrage", "Fragerecht", "die
  zweite Runde", "Themenkomplex"); a follow-up when it calls a Nachfrage ("Eine weitere Nachfrage hat …", "zu diesem
  Komplex", "hat sich gemeldet") or the member opens with "Meine Nachfrage …". A follow-up by the asker is a
  `nachfrage`, by another member a `zusatzfrage`.
- Where the presidency only names the member or their fraction, the structure decides: a member who asks again after
  the answer opened a question; one who asks once followed up. "Für die Fraktion … hat … das Wort" opens a question in
  a Fraktionsrunde (21/49) and calls the fractions' follow-ups in turn in others (21/58), so it decides nothing.

Who was questioned is read from the `einleitung` and `antwort` turns. Measured on 2026-10-06 (26 Befragungen, WP 21):
46 opening statements, 450 questions (29 without the asker's Nachfrage), 429 Nachfragen, 629 Zusatzfragen, 1,493
answers.

### Question texts

**question_text** `*id ("<drucksache_number>/<number>/<part>"), vorgang_id →vorgang, drucksache_number (the Drucksache listing the question: the Fragen-Drucksache, the Anfrage), drucksache_id →drucksache, position (order within the source document), part (vorbemerkung_fragesteller | frage | vorbemerkung_bundesregierung | antwort | anlage), number ("6", "3a"; an annex's; NULL for a preliminary remark), text (paragraphs joined by blank lines, tables left out), name (as printed: the asker; the answerer with office, "Parl. Staatssekretärs Stefan Rouenhoff"; the ministry of an answer to an Anfrage), answerer_person_id →person, answer_date, thread_id →speech (answered in the Fragestunde: the first turn), source_url, source_document_id, retrieved_at`

**question_table** `*id ("<question_text_id>/<k>"), question_text_id →question_text, position (k), after_paragraph (how many of the text's paragraphs come before the table), cells (JSON {"caption", "head", "body", "foot"}: rows of cells, a cell its text or {"text", "colspan", "rowspan"}; a table whose cells are not read: {"extracted": false, "page": n})`

**question_parse** `*vorgang_id →vorgang, status (complete | partial | unanswered | failed), questions (read), answered (… of them with an answer), source_document_id (the document read)` — per Vorgang, so a page can tell "not answered yet" from "answered, but not read". Kleine and Große Anfragen: `unanswered` when DIP lists no answer, `failed` when its PDF is not read (not fetched yet, or no question found), `complete` when every question read has an answer, else `partial`. Mündliche Fragen in the protocols: `complete` (answered in writing or in the Fragestunde) or `unanswered`. Schriftliche Fragen: `complete`, `unanswered` (the Sammeldrucksache prints no answer) or `failed` (not read: the Sammeldrucksache is not fetched yet, or the question was not found in it). Recomputed after every DIP ingest.

The texts of the questions to the government. Mündliche Fragen come from the Plenarprotokoll, which prints every one
of them (`bdf/parse_question_texts.py`); Kleine and Große Anfragen from the PDF of the answer (`bdf/parse_answers.py`);
Schriftliche Fragen from the PDF of their weekly Sammeldrucksache (`bdf/parse_schriftliche.py`); see below.

**Mündliche Fragen:**

- **Called in the Fragestunde**: the chair's call ("Wir kommen zur Frage 3 des Abgeordneten Bernd Schattner, AfD:")
  and the question it reads out; a call is a chair paragraph naming "Frage <n>" followed by the question before any
  speech. The answer and the follow-ups are the turns (`question_turn`), so there is no `antwort` row.
- **Answered in writing**: the annex "Schriftliche Antworten auf Fragen der Fragestunde (Drucksache …)", one entry per
  question (or "Fragen 32 und 33"): the asker, the question, "Antwort des …", the answer, with tables kept as cells in
  `question_table`. A joint answer is stored with each question it answers. An answer not there by the editorial
  deadline is printed in a later protocol, which then holds the question too (protocols are ingested in order). A
  misprinted annex title (21/20: "Drucksache 21/483" for 21/1483) takes the sitting's own Fragestunde Drucksache.

Rows are replaced with their protocol on every ingest. After the DIP ingest, `ingest_question_links` sets
`vorgang_id` (and the Fragestunde turns' `vorgang_id`), `answerer_person_id` and `drucksache_id`: DIP records each
question's Frage activity in a protocol with the asker and the page but not the number, so within a protocol one
asker's questions are paired with the Vorgänge DIP lists on the same Fragen-Drucksache, by the words the question
shares with the Vorgang's title, else in order (DIP's page order can differ from the numbers: 21/49). A question left
over takes an unpaired Vorgang of the same asker on its Fragen-Drucksache (DIP may record the Frage in a later
protocol). Checked: no pair of one asker's questions shares more words with the other's title than with its own.

Measured on 2026-10-06 (25 Fragen-Drucksachen in 27 protocols): 1,613 questions (169 called in the Fragestunde),
1,580 with their Vorgang; 1,442 written answers, 74 tables in 54 of them.

**Kleine and Große Anfragen.** The Antwort-Drucksache reprints the whole Anfrage, so its PDF alone gives every part:
the askers' preliminary remark and questions (set smaller, 9.6 pt), the government's preliminary remark and answers
(10.7 pt), annexes; the ministry and the date come from the note on page 1. DIP's `drucksache-text` loses the type
sizes and flattens tables (docs/plan.md, step 3), so `bdf fetch answers` (and `update`, after DIP) downloads the PDF of
every answer the DIP data lists, and `ingest_answers` reads it with pdfplumber; the rules are in the module's
docstring: hanging question numbers, joint answers stored with each question, sub-questions answered one by one as
"3a", misprints, repeated questions, tables cut out and joined across pages, annexes. A parse takes up to a minute for
a long answer and is cached next to the PDF (`<pdf>.answer.json`, by file and parser version). `ingest_question_links`
gives every row the answer's Vorgang and the Anfrage's DIP id.

Measured on 2026-10-06 on 149 random answers and the 7 answers to Große Anfragen online: every answer gave its
Anfrage, ministry and date; the questions of every answer form 1 … N without gaps (sub-questions besides), each with
an answer; 1,542 tables, 108 not read (above).

**Schriftliche Fragen.** Each week's questions and answers are printed together in a Sammeldrucksache ("Schriftliche
Fragen mit den in der Woche vom … eingegangenen Antworten der Bundesregierung"), fetched with the answers to Anfragen
(DIP type "Schriftliche Fragen") and read by `bdf/parse_schriftliche.py`, with the helpers of `bdf/parse_answers.py`
(rules in its docstring): per question the number, the asker and Fraktion (left column), the question (right column),
the ministry, the answerer's heading with the date ("Antwort des Parlamentarischen Staatssekretärs … vom 8. Juli
2026") and the answer; joint answers are stored with each question; reading stops at "Berlin, den …", before the
annexes some Sammeldrucksachen carry. Rows are keyed by the Sammeldrucksache and the number, `21/7052/36/frage`;
`name` holds the asker on the question, the answerer's heading on the answer. `ingest_question_links` takes the
Vorgang DIP gives the number ("36", or "36, 37" for one Vorgang over two questions), if DIP names the same asker; a
question left over takes the Vorgang of the same asker in the Sammeldrucksache that no question took, the one with the
closest number (DIP shifts some numbers: 21/7052 gives 2 to the asker of 3); where DIP keeps one question as two
Vorgänge (21/297, 77), the later one. The answerer comes from DIP's Antwort activity.

Measured on 2026-10-06 on all 75 Sammeldrucksachen the DIP data lists: 9,862 questions, each with an answer, the
numbers as DIP has them except where DIP lacks questions (21/7670, 21/7980) or misnumbers one (21/4006); 9,758 linked
to their Vorgang, 7,474 answers with the answerer's person; 1,507 tables, 4 not read; 2 answers without a date (the
year misprinted, "202S", "20626").

### Chair text and decisions

**agenda_item_paragraph** `*id ("<agenda_item_id>/<position>"), agenda_item_id →agenda_item, position, kind (chair | comment | procedural), text, source_url, source_document_id, retrieved_at`
— the text directly under `<tagesordnungspunkt>` outside any `<rede>` and outside a Fragestunde speech: the presidency calling items, putting questions to the vote and reading out results. `procedural` = the `T_*` agenda lines. The roll-call name lists (`AL_Namen`…) are not kept.

Every WP21 Fragestunde (25 agenda items, not just early ones) has no `<rede>` at all: the question, the answer and every Nachfrage are `<p klasse="redner">` paragraphs directly under `<tagesordnungspunkt>`. `parse_protocol._is_fragestunde` detects such an item structurally (no `<rede>`, at least one direct-child `<p klasse="redner">`) and `_split_fragestunde` turns each run starting at one of those paragraphs into a `speech` of `kind = 'fragestunde'`, the same way a normal `<rede>` is split; the presidency's own framing text between them stays `agenda_item_paragraph` (`chair`).

**agenda_sub_item** `*id ("<agenda_item_id>/<label>", e.g. "21/96/6/41b"), agenda_item_id →agenda_item, label ("41b"; Zusatzpunkte "ZP8", "ZP10a"), position (order within the item), title (this sub-item's title lines joined with " | "), drucksache_numbers (JSON array), first_paragraph, last_paragraph (positions in agenda_item_paragraph), source_url, source_document_id, retrieved_at, no_debate (0 | 1)`
— one `<tagesordnungspunkt>` is sometimes a block of many real items voted one by one ("Ich rufe auf die Tagesordnungspunkte 41b bis 41s. Es handelt sich um die Beschlussfassung zu Vorlagen, zu denen keine Aussprache vorgesehen ist."; 33 such items in the 97 sittings to 25 September 2026). Each is announced by a chair paragraph that is only the call-up ("Tagesordnungspunkt 41c:", "Zusatzpunkt 8:", "Wir kommen zu Tagesordnungspunkt 12k:"); `bdf/parse_sub_items.py` cuts the item there, the sub-item taking the procedural title lines and Drucksache lines up to the next call-up. Only an item with at least two distinct call-ups gets sub-items; all other items stay the unit, and the parent row is unchanged either way (title, Drucksachen, ids). Not split: a debated pair or triple ("Tagesordnungspunkte 7a und 7b", call-ups with words after them) and lettered lists that no call-up announces ("40 a) … b) …", Überweisungen im vereinfachten Verfahren). `no_debate` is the chair's statement made before the call-up (or in the sub-item), so the whole block carries it.

**agenda_item_vorlage** `*id ("<agenda_item_id or sub_item_id>/<drucksache_number>"), agenda_item_id →agenda_item, sub_item_id →agenda_sub_item (NULL when the Drucksache is listed on the item itself), drucksache_number, vorgang_id →vorgang, source_url, source_document_id, retrieved_at, via (title | decision)`
— the Drucksachen of `agenda_item.drucksache_numbers` as rows (`via = 'title'`), plus each Drucksache a decision taken under the item names that the item's title does not (`via = 'decision'`, provenance of the decision): mostly the Entschließungsanträge to a bill, debated with it and voted on after it, which the printed title leaves out. An item with sub-items lists each Drucksache under its sub-item (and any Drucksache outside all of them under the item), so one query over `agenda_item_id` sees every Drucksache once. `vorgang_id` is the Vorgang of `vorgang_drucksache` when the Drucksache has exactly one, else NULL; Bundesrat Drucksachen (their numbers collide with the Bundestag's) and Unterrichtungen shared by several Vorgänge (the § 80 GO-BT lists) are left out of that lookup. Rebuilt in full by `ingest_vorlagen` after the DIP ingest.

**decision** `*id, sitting_id →sitting, agenda_item_id →agenda_item, n, position, kind (namentlich | handzeichen), subject, drucksache_number, result (angenommen | abgelehnt | NULL), roll_call_vote_id →roll_call_vote, text, source_url, source_document_id, retrieved_at, sub_item_id →agenda_sub_item, vorgang_id →vorgang, dip_position_id, dip_result`
— one row per decision on substance announced by the chair (`bdf/parse_decisions.py`). Roll-call rows take the id of their `roll_call_vote` (`21/90/7`; `21/90/n<k>` without one), show-of-hands rows `<sitting>/p<paragraph>` (`21/90/p61`): the running number, over the sitting's chair text (`parse_decisions.chair_stream`), of the paragraph that asks the vote ("Wer stimmt dafür?", else its result sentence); a second or third vote asked in the same paragraph gets `-2`, `-3`. The id depends on where the vote stands in the protocol, not on how many decisions were found before it, so a parser that finds or drops a decision leaves the others' ids (and the cards' page addresses) alone; measured against the parser change of #29 (20 decisions found, 8 dropped), all 1,205 decisions both versions find keep their id, where the old count `h<n>` changed 148. An id changes only when the protocol's text changes: when the final version of a preliminary protocol replaces it (its paragraphs are counted anew), or when the chair text is split into paragraphs differently. Until 2026-10 the ids were `<sitting>/h<n>`, counting the decisions of the sitting. `n` counts per kind within the sitting, `position` orders all decisions of the sitting. `text` is the chair's words the row was read from. Procedure (Überweisung, Tagesordnung, Aufsetzung …) and elections are not decisions. `sub_item_id` is the sub-item whose call-up the chair had spoken last where the decision was read (NULL for items without sub-items, and for a decision moved to another item); it also supplies what the chair leaves out: the number of a Sammelübersicht ("Auch diese Sammelübersicht ist angenommen" is 305 under "Tagesordnungspunkt 41h") and a lone Drucksache. `vorgang_id` is the decision's only Vorgang in `decision_vorgang` (below), else NULL.

`dip_position_id`, `dip_result`: the DIP Vorgangsposition (BT, this sitting's Plenarprotokoll) whose `beschlussfassung` names the decision's Drucksache, and its `beschlusstenor`, when exactly one does; `bdf query decision-check` compares them with the protocol.

**decision_vorgang** `decision_id →decision, vorgang_id →vorgang, via` (composite PK)
— the Vorgänge a decision concerns, from the most precise source there is: `roll_call` (the roll call's Vorgang, from DIP's Namentliche Abstimmung paired by page), else `dip_step` (DIP's BT steps in the same sitting whose `beschlussfassung` names one of the decision's Drucksachen), else `drucksache` (every Vorgang of the decision's and its roll call's Drucksachen, with the same exclusions as `agenda_item_vorlage`). `decision.vorgang_id` is its only row, else NULL. Consumers read a decision's Vorgänge here and nowhere else. Rebuilt by `ingest_vorlagen`; inherits provenance from `decision`.

**decision_fraction** `decision_id →decision, fraction, position (yes | no | abstain)` (composite PK)
— fraction positions of show-of-hands decisions as the chair states them; inherits provenance from `decision`. For roll-call votes use `individual_vote`.

`roll_call_vote.agenda_item_id` is its decision's agenda item, else the agenda item of the same sitting listing one of its Drucksachen.

### Interjections

**interjection** `*id ("<speech_id>/<paragraph>/<part>/<actor>"), speech_id →speech, paragraph (speech_paragraph.position), part, kind (beifall | zuruf | gegenruf | lachen | heiterkeit | widerspruch | zustimmung | unruhe | other), actor (fraction | members | person | house | unknown), fraction, person_id →person, name, text (the words of a Zuruf), to_person_id →person, to_name (addressee named in the comment; NULL = the speaker)`

Parsed from the `comment` paragraphs (`bdf/parse_comments.py`) in the same transaction as the protocol's speeches,
so it inherits provenance through `speech_id`. "Beifall bei der SPD sowie bei Abgeordneten der CDU/CSU" is two
rows: `fraction` SPD and `members` CDU/CSU. A named interjection ("Name [Fraktion]: …") is a `zuruf` by a `person`.

### abgeordnetenwatch.de

**aw_profile** `*aw_politician_id, person_id →person (NULL if unmatched), url (public profile), questions, questions_answered (citizen questions on the profile, lifetime totals), source_url, source_document_id, retrieved_at`

**side_job** `*id (aw sidejob id), wahlperiode, person_id →person (via the aw mandate, NULL if unmatched), aw_mandate_id, label (the entry as published), job_title_extra, category (aw's Bundestag Verhaltensregeln category), income_level (Stufe 0..10, NULL if none published), income_range (the Stufe's range as published), income (exact amount if aw has one, NULL otherwise), interval (einmalig | monatlich | jährlich), additional_information, organization_id, organization, city, topics (JSON list of aw topic labels), created, data_change_date, source_url, source_document_id, retrieved_at`
— Nebentätigkeiten (side jobs) reported under the Bundestag's Verhaltensregeln, republished by abgeordnetenwatch as CC0. One row per aw sidejob record; `ingest_side_jobs` replaces a Wahlperiode's rows wholesale, so entries aw withdraws disappear. Facts as published only: no linking of income to speeches or votes, no ranking, no sums across members (`docs/decisions.md`).

### Election (Bundeswahlleiterin)

**constituency** `*id ("btw25/114"), election, number, name, state, seat_party (party whose candidate got the seat; NULL when the winner had no Zweitstimmendeckung), electorate, voters, source_url, source_document_id, retrieved_at`

**constituency_result** `*id ("btw25/114/<group order>/<vote>"), election, constituency_number, group_kind (party | individual), party, vote (1 Erststimme | 2 Zweitstimme), votes, percent, source_url, source_document_id, retrieved_at`

**election_candidacy** `*id ("btw25/<row>"), election, person_id →person (NULL if unmatched), last_name, first_names, birth_year, party, elected_via (constituency | list), constituency_number (won there, or stood there), first_vote_percent (constituency winners), list_state, list_position, occupation, source_url, source_document_id, retrieved_at`

**constituency_municipality** `*id ("btw25/<ags>/<number>"), election, ags (Amtlicher Gemeindeschlüssel, 8 digits), name, district (Kreisname), state, constituency_number, split (1 = the Gemeinde is split across Wahlkreise, one row per Wahlkreis), source_url, source_document_id, retrieved_at`

**mandate_successor** `*id ("btw25/<Bek.-Nr.>"), election, predecessor_person_id →person, predecessor_name, predecessor_party, predecessor_state, predecessor_seat ("LL 003" | "WK 044"), reason (Ablehnung, Mandatsverzicht, Tod, …), person_id →person, name (as printed, "Asghari, Dr. Reza"), birth_year, party, state, seat ("LL 018" | "WK 282"), from_date (Beginn der Mitgliedschaft), source_url, source_document_id ("… Mandatsnachfolger (abgerufen <date>)"), retrieved_at`
— the Bundeswahlleiterin's list "Veränderungen im 21. Deutschen Bundestag" (a one-page PDF, parsed by `parse_wahl.parse_successors`; rows that look like a row but do not parse are reported by ingest). `state` is the Land of the successor's seat, for Nachrücker the Land of the Landesliste; codes as in `mandate.state` (the list's "NRW" is NW). The list lags: on 2026-10-04 it was last changed on 2026-06-23 and has 9 rows, while Katrin Zschau (SPD) votes since 2026-09-24; a successor not yet on it has no row. The Stammdaten lag too (file of 2026-04-29), so for Glaser, Naser, Breilmann and Zschau `mandate` has no WP 21 row; the list is where their Land comes from.

Only the candidates elected on election day are in the Gewählte file; Nachrücker are in `mandate_successor`.
A list member's own first-vote share is the party's `vote = 1` row in `constituency_result`
for their `constituency_number`.

### Portraits and government

**person_photo** `*person_id →person, image_url, credit (photographer / rights holder as printed, without "©"), bio_url (bundestag.de biography; NULL for Commons), local_path (relative to data/raw/), source_url, source_document_id, retrieved_at`
— one portrait per person, replaced wholesale on ingest. MdBs: the bundestag.de biography card; the credit is the
caption of the card's image ("© Sanae Abdi/SPD-Fraktion"). Government members without a card (non-MdB ministers):
the Wikidata P18 image from Commons, credit "<author>, <licence>", `source_url` the Commons file page.

**government_role** `*id, person_id →person, wikidata_qid, name, office, department, kind (kanzler | minister | staatsminister | parl_sts | beamteter_sts), from_date, to_date, source_url, source_document_id, retrieved_at, source_kind (wikidata | stammdaten | protocol)`
— the federal government since 2025-05-06, replaced wholesale on ingest, merged (`bdf/government.py`) from three
sources:
- **wikidata**: one "position held" (P39) statement starting on or after 2025-05-06. `kind` comes from the
  position's class: Bundeskanzler; subclasses of Bundesminister and the Chef des Bundeskanzleramtes → minister;
  Staatsminister (Bund) and the BKM → staatsminister; subclasses of Parlamentarischer Staatssekretär and beamteter
  Staatssekretär. `department` is the statement's "of" qualifier or the position's "directs" (P2389).
- **stammdaten**: a membership of kind other since 2025-05-06 whose function (`role`) is Bundeskanzler(in),
  Bundesminister(in), Staatsminister(in) or Parlamentarische(r) Staatssekretär(in); the institution is the
  department. Exact dates, MdBs only.
- **protocol**: the role printed for a speaker (`speech.speaker_role`, else `person.role` of a non-MdB), parsed into
  kind and department ("Parl. Staatssekretärin bei der Bundesministerin für …", "Bundesminister des Auswärtigen" →
  Auswärtiges Amt, "Staatsminister beim Bundeskanzler" / "für Kultur und Medien" → Bundeskanzleramt; Land offices
  and Beauftragte are not government roles). `from_date`/`to_date` are the first and last sitting that prints the
  role for the person: **evidence, not appointment dates** (consumers: "belegt ab … (Plenarprotokoll)").
  Provenance is the protocol of the first sitting.

One row per person + kind + department (departments compared on a key that ignores "für"/"und", so the
Stammdaten's "Justiz und Verbraucherschutz" meets "Justiz und für Verbraucherschutz"). The row takes dates and
provenance from the best source present, wikidata > stammdaten > protocol, and names it in `source_kind`; `office`
is Wikidata's label, else a generic one ("Parlamentarischer Staatssekretär beim Bundesminister der Finanzen");
`department` is Wikidata's name, else the one read from the protocol, else the Stammdaten institution. `id` is the
Wikidata statement id when Wikidata is the source, else `<source_kind>:<person_id>:<kind>:<department key>`.
An open Kanzler or Bundesminister role from Wikidata or the Stammdaten is closed the day before the protocols first
show another person in that office (`ingest` prints each such inference). `queries.government(on=…)` treats a
protocol row as held after its last evidence until contradicted by a later role of the person or a later holder of
the same single-holder office.

**Reading `to_date` by `source_kind`.** For `wikidata` and `stammdaten`, `to_date` is the end of office (NULL:
still in office; possibly an end inferred from the protocols, see above). For `source_kind = 'protocol'` it means
**"last seen in a protocol"**: the date of the last sitting that prints the role, *not* the end of office, and it is never NULL.
Consumers must not show it as "bis …"; show it as "zuletzt belegt …" and treat the role as current unless another
row contradicts it (`government.held_on`). `bdf query government` prints protocol rows as "belegt <from>..<to>
(Plenarprotokoll)".

**Stale protocol roles.** A protocol-only role that is still counted as held at the newest sitting but whose
`to_date` lies more than 90 days (`government.STALE_AFTER_DAYS`) before that sitting is *stale*: probably ended,
but no source says so. It stays current (no automatic end date); `bdf query stale-roles [--days N]` lists these rows
with `days_since_seen` and `newest_sitting`, and `bdf update` prints them as a warning after ingest. The warning
does not change the exit code. The fix is upstream: once Wikidata or the Stammdaten carry the role, it is no longer
protocol-only; a successor in a Kanzler/Bundesminister office takes it out of the held roles too.

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
6. **Biography card → person**: printed name ("Aken, Jan van", "Schneider (Erfurt), Carsten", titles stripped)
   with the shared name index; if that is ambiguous, a unique WP member with the same surname, first given name
   and fraction. A card whose fraction differs from the store's is reported.
7. **Government role → person**: Stammdaten and protocol roles carry the person id; Wikidata roles: `person.wikidata_qid` (from abgeordnetenwatch), then the full name with an equal
   or unknown birth date (non-MdB speakers from the protocols have none), then the shared name index with the
   birth year. A match by name writes the QID onto the person. The rest get a person row with `id` = QID and
   `is_mdb = 0`; such a row is deleted again once the person is matched.

## Incremental updates

- Unit of work is a date range (a sitting week). `fetch` downloads anything in the range
  that is not already on disk (files are immutable; a re-fetch overwrites only with
  `--force`). `ingest` upserts by primary key, so re-running is safe and idempotent.
- Stammdaten and DIP persons are re-fetched whole (small) and upserted.
- Corrections to protocols are rare; `fetch --force` + `ingest` handles them.

### Health report

After every ingest `update` takes a snapshot of the store (`bdf/health.py`): the rows of every table and the known
problem counts, saved as `data/health/<date>.json` and compared with the newest earlier snapshot. The comparison is
printed in the run's log; `bdf health` prints it for the current store without saving. The run fails (exit code 1;
systemd reports it to Gatus, which alerts via ntfy) only when something clearly got worse:

- a table that had rows is empty or gone, or lost more than 5 % of its rows (rows normally only grow; this covers
  every table, so a run that suddenly loses `vorgang_referral` rows fails);
- a problem count rose by more than its threshold since the last snapshot:

| Problem | Fails at a rise of |
|---|---|
| `protocol_gaps`: Beratungen without an agenda item, not counting preliminary protocols without a PDF part | 10 |
| `decision_results_disputed`: decisions whose result DIP records differently (`bdf query decision-check`) | 5 |
| `decisions_missing`: DIP decisions with no decision row (`decision-check`) | 10 |
| `unmatched_votes`: roll-call vote rows without a person | 100 |
| `unlinked_roll_calls`: roll-call votes without a Vorgang | 5 |
| `vorlagen_without_vorgang`: Drucksachen on agenda items without a Vorgang (DIP links new ones days later) | 150 |
| `askers_without_person`: DIP persons asking Fragen without a person | 10 |
| `unmatched_elected`, `unmatched_successors`: candidates and Mandatsnachfolger without a person | 3 |

`protocol_gaps_preliminary`, `preliminary_sittings`, `stale_roles`, `placeholder_speakers` and `unresolved_authors`
are reported only. A first snapshot, a new table and a new problem count never fail. A source that cannot be
fetched fails the run as before. On 2026-10-04 the live store had 23 protocol gaps, 17 preliminary sittings, no
unmatched votes and no unlinked roll calls, 188 Vorlagen without a Vorgang, 30 unresolved DIP authors.

### Preliminary protocols

bundestag.de serves a protocol's XML at its final URL (`btp/21/21031.xml`) before the final version exists. The
preliminary version carries a note, "Der gesamte und damit endgültige Stenografische Bericht der 31. Sitzung wird
am … veröffentlicht", and may end hours before the sitting did. On 2026-10-01, 17 of 97 WP 21 protocols were still
preliminary, some for a year (21/14, announced for 2025-07-01); 21/31 ends after TOP 22, the PDF goes on to TOP 31.
DIP's `plenarprotokoll-text` was no help: for 21/31 it is the same preliminary text. The PDF at the same address
(`btp/21/21031.pdf`) is the final version.

- **Detect** (`protocol_status.status`): the note (also "vorläufiger Stenografischer Bericht") anywhere in the
  document text, and the announced date. Stored on `sitting`.
- **Re-fetch** (`fetch_bundestag.refetch_preliminary`, run by `update` on every run, by hand with
  `bdf fetch protocols --preliminary`): every preliminary XML on disk is downloaded again and replaces the file;
  `ingest` then replaces the sitting's speeches, items and paragraphs as for any re-ingested protocol (agenda items
  it no longer has are dropped).
- **PDF part** until then (`fetch_bundestag.fetch_preliminary_pdfs`, same runs): the PDF of each preliminary
  protocol, fetched again every run and written only when it changed. `parse_protocol.parse` hands a preliminary
  XML with a PDF beside it to `protocol_pdf.merge`, which reads the PDF column by column (pdfplumber; font size and
  weight tell speaker lines, body text, agenda titles and comments apart), finds where the XML's text ends (its last
  ~300 letters, normalized, in the PDF's text) and appends what follows as the elements the XML would have had:
  `<tagesordnungspunkt>` at each call-up ("Ich rufe den Tagesordnungspunkt 29 auf:") with `T_*` title paragraphs,
  `<rede>` with `<redner>`, `<name>` for the presidency, `<kommentar>`, `<p>`; it stops at "(Schluss: … Uhr)" and
  leaves out the printed name lists of roll-call votes. Speakers get the id another protocol's XML gives the same
  printed line or name; one never seen gets `pdf-<name>` (a non-MdB person row) and ingest names them. Everything
  downstream (decisions, sub-items, interjections, Vorlagen) reads these rows like any other. Reading a PDF takes
  about 30 s; the lines are kept beside it (`<pdf>.lines.json`) while the PDF is unchanged.
- **Check** (`bdf query protocol-gaps`): per sitting that is preliminary or has one, every Beratung DIP places in
  its Plenarprotokoll (BT `vorgang_position` of kind Plenarprotokoll with pages, position "…Beratung…") that no
  agenda item or sub-item of the sitting carries (a Drucksache of the Vorgang, or an `agenda_item_vorlage` row for
  it), with its cause: `preliminary` (no PDF part read), `after_xml_end` (after the XML's end, so the PDF part
  missed it) or `in_protocol` (the protocol has it: another Drucksache on the item, or the parser misses it).
  An item's Drucksachen include those only a decision under it names (`via = 'decision'`); DIP's
  "Geschäftsordnungsantrag …" positions are left out (a motion on the agenda, not a Beratung of the Vorgang).

  **Known gaps** (live store, 2026-10-04: 23 Beratungen, none a parser miss):

  | Cause | n | Sittings |
  |---|---|---|
  | A Vorlage from WP 20: the item names the WP 20 Drucksache, DIP's new WP 21 Vorgang only later ones | 10 | 21/6 (Wehrbeauftragte 20/15060), 21/10 (Entlastung 20/12195), 21/14, 21/16–21/19 (Finanzplan 20/12401), 21/40, 21/50, 21/65 |
  | Constitution and procedure without a Drucksache on the item: the constituent sitting, the Kanzlerwahl, a number of members fixed at the opening; the AfD's amendments to the Geschäftsordnung (21/4, 21/5), which DIP files under Vorgänge with only 21/2196 | 7 | 21/1 (5), 21/2, 21/21 |
  | A change of committee referral announced at the opening, not an agenda item | 3 | 21/49, 21/52, 21/82 |
  | Entschließungsanträge to the budget, debated with their Einzelplan (items without Drucksache), voted on in 21/25 | 2 | 21/23 |
  | An Anlage: a corrected vote from sitting 13 | 1 | 21/15 |

  Seven of these Vorgänge (three WP 20 reports, four procedure items) have no `vorgang` row: Vorgänge are
  fetched through WP 21 Drucksachen, and these have none.

## CLI

```
bdf fetch stammdaten
bdf fetch protocols --wp 21 --from 88 --to 90
bdf fetch protocols --wp 21 --preliminary           # the preliminary protocols on disk again, and their PDFs
bdf fetch votes --from 2026-07-06 --to 2026-07-10
bdf fetch dip --from 2026-07-06 --to 2026-07-10     # drucksachen, authors, vorgänge, vorgangspositionen, persons
bdf fetch aw --wp 21
bdf fetch photos                                    # bundestag.de biography list + portraits
bdf fetch government                                # Wikidata roster + Commons portraits
bdf fetch answers --wp 21                           # PDFs of the answers to Anfragen and of Schriftliche Fragen
bdf ingest                                          # everything under data/raw → sqlite
bdf health                                          # rows per table and problem counts against the last update's snapshot
bdf query speeches   --person 11004006 --from … --to …
bdf query votes      --person 11004006 --from … --to …
bdf query drucksachen --person 11004006 --from … --to …
bdf query government [--date 2026-09-28]           # roles, optionally those held on a day
bdf query stale-roles [--days 90]                   # protocol-only roles not printed for >90 days before the newest sitting
bdf query photos     [--missing]                    # portraits with credit, or sitting members without one
bdf query decisions  --sitting 21/90                # decisions announced by the chair, fraction positions / roll-call totals
bdf query protocol-gaps                             # preliminary protocols, DIP Beratungen without an agenda item
bdf query decision-check                            # decisions DIP records with another result, DIP decisions not found
bdf query corpus     --from … --to …                # JSONL: one clean speech per line with speaker id, fraction, date, source
bdf export data/export                              # open data: one CSV.gz per table, datapackage.json, README.md
```

Every query prints the source pointer on each row.

## Open-data export

`bdf export <dir>` writes every table as `<table>.csv.gz` (UTF-8, header row, rows ordered by primary key, NULL as
an empty field, gzip without a timestamp so unchanged data gives identical files), a Frictionless
`datapackage.json` and a German `README.md` with the attribution each source requires. The data package lists per
table the fields with type (`string` / `integer` / `number` from the SQLite type) and the description from the
`-- …` comment in `db.SCHEMA` (parsed at export time, so the schema stays the only place to document a column),
the primary key, the single-column foreign keys, and the sources and licences (`export.TABLE_SOURCES`); at the top
the created timestamp and `latest_sitting_date`. Provenance columns are exported; `person_photo.local_path` (a path
on the build machine) is not, and no images are. The export is built in a hidden sibling directory and renamed into
place, so a failed run keeps the previous export. On the dev store (95 sittings) it is ~37 MB, two thirds of it
`speech_paragraph` and `speech`, and takes ~10 s; `frictionless validate datapackage.json` passes.
